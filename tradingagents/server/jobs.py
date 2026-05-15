"""Background job runner that wraps TradingAgentsGraph.propagate().

One JobRunner instance is held by the FastAPI app for the lifetime of the
process. It owns the asyncio.Semaphore that caps concurrency, the path to the
jobs SQLite DB, and the results directory.

Per-step status (LangGraph node name) is not tracked in M1 — jobs go
queued -> running -> (done | failed). Per-node progress is a clean follow-up
that drops in without changing the API surface.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
import uuid
from pathlib import Path
from typing import Any

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.report_writer import save_report_to_disk
from tradingagents.server import db
from tradingagents.server.schemas import CreateAnalysisRequest

logger = logging.getLogger(__name__)


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
    cfg["checkpoint_enabled"] = True  # design §4.2: crash-recovery for the web service
    return cfg


def _run_blocking(job_id: str, req: CreateAnalysisRequest, db_path: Path) -> None:
    """Synchronous body — runs inside a thread via asyncio.to_thread."""
    db.mark_running(db_path, job_id)

    cfg = _build_config(req)
    date_str = req.analysis_date.isoformat()

    try:
        # Lazy import: LangChain/LangGraph cold start is ~5s. Keeping it here
        # means the FastAPI process boots fast and tests that don't run jobs
        # don't pay the cost.
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        graph = TradingAgentsGraph(selected_analysts=list(req.analysts), config=cfg)
        final_state, _signal = graph.propagate(req.ticker, date_str)

        save_path = Path(cfg["results_dir"]) / req.ticker / date_str
        save_report_to_disk(final_state, req.ticker, save_path)

        report_path = save_path / "complete_report.html"
        db.mark_done(db_path, job_id, report_path=str(report_path))
    except Exception as exc:  # noqa: BLE001 — surface any failure to the user
        logger.exception("Job %s failed", job_id)
        # Truncate so a long traceback doesn't blow up the DB row.
        msg = f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()[-4000:]}"
        db.mark_failed(db_path, job_id, error=msg)


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
