"""Background job runner that wraps TradingAgentsGraph.propagate().

One JobRunner instance is held by the FastAPI app for the lifetime of the
process. It owns the asyncio.Semaphore that caps concurrency, the path to the
jobs SQLite DB, and the results directory.

Each analysis runs in a *subprocess* (tradingagents.server._worker). yfinance
holds a process-global YfData singleton and curl_cffi keeps C-level
connection pools / TLS state; in a long-lived uvicorn worker those
accumulate and reliably trip Yahoo's 429 even when fresh CLI processes from
the same IP succeed at the same moment. Subprocess isolation is the cheapest
way to give every analysis the same clean process state a CLI run has.

Per-step status (LangGraph node name) is not tracked in M1 — jobs go
queued -> running -> (done | failed). Per-node progress is a clean follow-up
that drops in without changing the API surface.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.server import db
from tradingagents.server.schemas import CreateAnalysisRequest

logger = logging.getLogger(__name__)

# Hard cap on a single analysis. Real runs are 3-8 min; 30 min covers
# pathological retry loops without leaving zombie subprocesses forever.
_WORKER_TIMEOUT_SECONDS = 1800


def _build_config(req: CreateAnalysisRequest) -> dict[str, Any]:
    cfg = DEFAULT_CONFIG.copy()
    cfg["llm_provider"] = req.llm_provider
    if req.deep_thinking_model:
        cfg["deep_think_llm"] = req.deep_thinking_model
    if req.quick_thinking_model:
        cfg["quick_think_llm"] = req.quick_thinking_model
    cfg["max_debate_rounds"] = req.max_debate_rounds
    cfg["max_risk_discuss_rounds"] = req.max_risk_discuss_rounds
    cfg["output_language"] = req.output_language
    # Originally True (design §4.2: container-restart recovery). Empirically
    # the LangChain/DeepSeek tool-call handshake fails on Market Analyst's
    # first turn ~50% of the time, and when that happens mid-batch LangGraph
    # persists an assistant message with `tool_calls` but no matching `tool`
    # responses. Subsequent runs resume from that poisoned state and 400
    # deterministically on DeepSeek's "insufficient tool messages" check —
    # turning one transient flake into an unrecoverable loop. Until the
    # upstream handshake is sturdier, treat each run as independent.
    cfg["checkpoint_enabled"] = False
    return cfg


def _find_project_dotenv() -> str | None:
    """Locate the project's .env so the subprocess can load it."""
    try:
        from dotenv import find_dotenv

        path = find_dotenv(usecwd=True)
        return path or None
    except ImportError:
        return None


def _run_blocking(job_id: str, req: CreateAnalysisRequest, db_path: Path) -> None:
    """Synchronous body — runs inside a thread via asyncio.to_thread.

    Spawns a subprocess to do the actual analysis so yfinance and friends
    get clean process state per run. The parent stays alive across all
    analyses; only the worker is short-lived.
    """
    db.mark_running(db_path, job_id)

    cfg = _build_config(req)
    payload = {
        "config": cfg,
        "ticker": req.ticker,
        "analysis_date": req.analysis_date.isoformat(),
        "analysts": list(req.analysts),
        "dotenv_path": _find_project_dotenv(),
        # Worker uses these to write live current_step updates into the same
        # jobs SQLite the parent is polling.
        "db_path": str(db_path),
        "job_id": job_id,
    }

    try:
        result = subprocess.run(
            [sys.executable, "-m", "tradingagents.server._worker"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=_WORKER_TIMEOUT_SECONDS,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired:
        db.mark_failed(
            db_path,
            job_id,
            error=f"analysis timed out after {_WORKER_TIMEOUT_SECONDS}s",
        )
        return
    except Exception as exc:  # noqa: BLE001 — surface spawn errors
        logger.exception("Job %s failed to spawn worker", job_id)
        msg = f"{type(exc).__name__}: {exc}"
        db.mark_failed(db_path, job_id, error=msg)
        return

    # Parent stdout is the result JSON; stderr is the agent's chatter.
    # On crash (non-zero exit, garbled stdout), surface whatever stderr we have.
    stdout = (result.stdout or "").strip()
    if not stdout:
        err_tail = (result.stderr or "")[-2000:]
        db.mark_failed(
            db_path,
            job_id,
            error=f"worker exit {result.returncode}, no result on stdout\n\nstderr tail:\n{err_tail}",
        )
        return

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        err_tail = (result.stderr or "")[-2000:]
        db.mark_failed(
            db_path,
            job_id,
            error=f"worker returned non-JSON stdout (exit {result.returncode}):\n{stdout[:500]}\n\nstderr tail:\n{err_tail}",
        )
        return

    if data.get("status") == "done":
        db.mark_done(db_path, job_id, report_path=data["report_path"])
    else:
        db.mark_failed(db_path, job_id, error=data.get("error", "<no error message from worker>"))


class JobRunner:
    def __init__(self, db_path: Path, max_concurrent_jobs: int = 2) -> None:
        self.db_path = db_path
        self.semaphore = asyncio.Semaphore(max_concurrent_jobs)
        self._tasks: set[asyncio.Task[None]] = set()
        db.init_db(db_path)

    async def _run(self, job_id: str, req: CreateAnalysisRequest) -> None:
        async with self.semaphore:
            await asyncio.to_thread(_run_blocking, job_id, req, self.db_path)

    def submit(self, req: CreateAnalysisRequest) -> str:
        job_id = uuid.uuid4().hex
        db.create_job(
            self.db_path,
            job_id=job_id,
            ticker=req.ticker,
            analysis_date=req.analysis_date.isoformat(),
            config=req.model_dump(mode="json"),
        )
        task = asyncio.create_task(self._run(job_id, req))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job_id
