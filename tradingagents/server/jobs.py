"""Background job runner that wraps TradingAgentsGraph.propagate().

One JobRunner instance is held by the FastAPI app for the lifetime of the
process. It owns the asyncio.Semaphore that caps concurrency and the path
to the jobs SQLite DB.

Each analysis runs **in the parent uvicorn process** via
`asyncio.to_thread(...)`. An earlier iteration spawned a subprocess per
analysis on the theory that yfinance's process-global state was tripping
Yahoo's 429, but empirical testing (Agent A's matrix in docs) showed the
real culprit was the LangChain/DeepSeek tool-call handshake — addressed
by `invoke_with_tool_call_repair` in agents/utils/agent_utils.py and by
disabling `checkpoint_enabled`. With yfinance ruled out, in-process is
simpler and saves ~3–5s of subprocess startup per job.

Per-step status (LangGraph node name) is captured via
`callbacks.NodeProgressCallback` and lives in the jobs row's
`current_step` column; token usage + cost via `TokenUsageCallback` and
the three `prompt_tokens / completion_tokens / estimated_cost_usd`
columns. Both run as `graph_callbacks` on TradingAgentsGraph.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import traceback
import uuid
from pathlib import Path
from typing import Any

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.server import db
from tradingagents.server.callbacks import NodeProgressCallback, TokenUsageCallback
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
    # See history: originally True for crash-recovery, then flipped to False
    # because LangGraph persisting a half-written tool_calls handshake
    # poisoned the resume and turned transient flakes into permanent loops.
    cfg["checkpoint_enabled"] = False
    return cfg


def _run_blocking(job_id: str, req: CreateAnalysisRequest, db_path: Path) -> None:
    """Synchronous body — runs inside a worker thread via asyncio.to_thread."""
    db.mark_running(db_path, job_id)

    cfg = _build_config(req)
    date_str = req.analysis_date.isoformat()
    token_cb = TokenUsageCallback()

    try:
        # Lazy import: LangChain/LangGraph cold start is ~5s. Keeping it in
        # the worker thread means the FastAPI process boots fast.
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        from tradingagents.report_writer import save_report_to_disk

        graph_cbs: list[Any] = [token_cb, NodeProgressCallback(db_path, job_id)]
        graph = TradingAgentsGraph(
            selected_analysts=list(req.analysts),
            config=cfg,
            graph_callbacks=graph_cbs,
        )
        final_state, _signal = graph.propagate(req.ticker, date_str)

        save_path = Path(cfg["results_dir"]) / req.ticker / date_str
        save_report_to_disk(final_state, req.ticker, save_path)
        report_path = save_path / "complete_report.html"
        db.mark_done(db_path, job_id, report_path=str(report_path))
    except Exception as exc:  # noqa: BLE001 — surface any failure to the user
        logger.exception("Job %s failed", job_id)
        msg = f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()[-4000:]}"
        db.mark_failed(db_path, job_id, error=msg)
    finally:
        # Telemetry is best-effort and must never break the result write.
        try:
            in_, out_, cost = token_cb.totals()
            db.set_telemetry(
                db_path,
                job_id,
                prompt_tokens=in_ or None,
                completion_tokens=out_ or None,
                estimated_cost_usd=cost,
            )
        except Exception:  # noqa: BLE001
            logger.debug("set_telemetry failed", exc_info=True)


class JobRunner:
    """Owns the jobs SQLite DB and the asyncio semaphore for concurrency.

    Supports an optional *snapshot* path: the runtime DB lives somewhere fast
    (e.g. /tmp/jobs.sqlite when the share can't host SQLite locks), and every
    ``snapshot_interval_seconds`` we copy a consistent backup to a durable
    location (e.g. Azure Files mount). On startup we restore the runtime DB
    from the snapshot if present, so history survives container restarts.
    """

    def __init__(
        self,
        db_path: Path,
        max_concurrent_jobs: int = 2,
        *,
        persist_path: Path | None = None,
        snapshot_interval_seconds: float = 30.0,
    ) -> None:
        self.db_path = db_path
        self.persist_path = (
            persist_path if persist_path and persist_path != db_path else None
        )
        self.snapshot_interval_seconds = snapshot_interval_seconds
        self.semaphore = asyncio.Semaphore(max_concurrent_jobs)
        self._tasks: set[asyncio.Task[None]] = set()
        self._snapshot_task: asyncio.Task[None] | None = None

        # Best-effort restore before init_db so a fresh container picks up
        # the last snapshotted state. init_db's idempotent migration handles
        # the case where the snapshot's schema is one revision behind.
        if self.persist_path and self.persist_path.exists() and self.persist_path.stat().st_size > 0:
            try:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.persist_path, self.db_path)
                logger.info(
                    "Restored jobs DB from snapshot %s (%d bytes)",
                    self.persist_path,
                    self.persist_path.stat().st_size,
                )
            except OSError:
                logger.warning(
                    "Could not restore jobs DB from %s; starting fresh",
                    self.persist_path,
                    exc_info=True,
                )

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

    # ----- snapshot lifecycle --------------------------------------------------

    async def start_snapshot_task(self) -> None:
        """Kick off the background snapshot loop. No-op when persist_path is
        unset (local dev) — runtime and snapshot are the same file."""
        if self.persist_path is None or self._snapshot_task is not None:
            return
        self._snapshot_task = asyncio.create_task(self._snapshot_loop())

    async def stop_snapshot_task(self) -> None:
        """Cancel the periodic loop and take one final synchronous snapshot
        so the very last writes get persisted before the container exits."""
        if self._snapshot_task is not None:
            self._snapshot_task.cancel()
            try:
                await self._snapshot_task
            except asyncio.CancelledError:
                pass
            self._snapshot_task = None
        if self.persist_path is not None:
            try:
                await asyncio.to_thread(db.snapshot_db, self.db_path, self.persist_path)
            except Exception:  # noqa: BLE001
                logger.warning("final snapshot failed", exc_info=True)

    async def _snapshot_loop(self) -> None:
        assert self.persist_path is not None
        while True:
            try:
                await asyncio.sleep(self.snapshot_interval_seconds)
                await asyncio.to_thread(db.snapshot_db, self.db_path, self.persist_path)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — never crash the loop on transient errors
                logger.warning("periodic snapshot failed", exc_info=True)
