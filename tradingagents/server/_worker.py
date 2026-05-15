"""Subprocess worker for a single TradingAgents analysis.

Invoked by [jobs.py](jobs.py)'s job runner as:

    python -m tradingagents.server._worker

with a JSON payload on stdin describing the analysis (config, ticker, date,
selected analysts). Writes a single-line JSON result to stdout on exit; all
agent / framework output is captured to stderr so the parent can parse the
result cleanly.

The whole point is process isolation: yfinance's process-global YfData
singleton and curl_cffi's C-level connection pool accumulate state in a
long-lived uvicorn worker and reliably trip Yahoo's 429 threshold. A fresh
Python process per analysis dodges that the way CLI invocations always have.
"""

from __future__ import annotations

import io
import json
import logging
import sys
import traceback
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class _NodeProgressCallback:
    """LangChain BaseCallbackHandler that writes the current LangGraph node
    name into the jobs SQLite row so polling clients see live progress.

    Implemented as a duck-typed handler (no inheritance) — LangChain only
    calls the methods we define, and avoiding the import keeps `_worker.py`
    light when LangChain isn't strictly needed.
    """

    def __init__(self, db_path: Path, job_id: str) -> None:
        self._db_path = db_path
        self._job_id = job_id
        # Import lazily so module import stays cheap.
        from tradingagents.server.db import set_current_step

        self._set = set_current_step

    def on_chain_start(self, serialized: dict[str, Any], *_args: Any, **kwargs: Any) -> None:
        # LangGraph tags node invocations with metadata.langgraph_node; that's
        # the only way to distinguish "this is a real pipeline step the user
        # cares about" from "this is an internal LangChain plumbing chain".
        meta = kwargs.get("metadata") or {}
        node = meta.get("langgraph_node")
        if not node:
            return
        try:
            self._set(self._db_path, self._job_id, str(node))
        except Exception:  # noqa: BLE001 — telemetry must never fail the run
            logger.debug("set_current_step failed", exc_info=True)


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        json.dump({"status": "failed", "error": f"invalid payload: {exc}"}, sys.stdout)
        return 2

    config = payload["config"]
    ticker = payload["ticker"]
    date_str = payload["analysis_date"]
    analysts = payload["analysts"]
    dotenv_path = payload.get("dotenv_path")
    db_path = payload.get("db_path")
    job_id = payload.get("job_id")

    # Load .env so the subprocess sees DEEPSEEK_API_KEY / OPENAI_API_KEY / etc.
    # without having to re-export them. Inherits from parent already, but this
    # is belt-and-braces in case the parent was started without them.
    try:
        from dotenv import load_dotenv

        if dotenv_path:
            load_dotenv(dotenv_path, override=False)
    except ImportError:
        pass

    # Hide all stdout from the analysis pipeline; the only stdout we want is
    # the final result JSON. LangGraph prints, LangChain warnings, anything
    # the framework emits — all routed to stderr.
    real_stdout = sys.stdout
    sys.stdout = sys.stderr

    result: dict[str, object]
    try:
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        from tradingagents.report_writer import save_report_to_disk

        progress_cbs = (
            [_NodeProgressCallback(Path(db_path), job_id)]
            if db_path and job_id
            else None
        )
        graph = TradingAgentsGraph(
            selected_analysts=analysts,
            config=config,
            graph_callbacks=progress_cbs,
        )
        final_state, signal = graph.propagate(ticker, date_str)

        save_path = Path(config["results_dir"]) / ticker / date_str
        save_report_to_disk(final_state, ticker, save_path)
        report_path = save_path / "complete_report.html"

        result = {
            "status": "done",
            "report_path": str(report_path),
            "signal": str(signal),
        }
    except Exception as exc:  # noqa: BLE001 — surface any failure to the parent
        result = {
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()[-4000:]}",
        }

    sys.stdout = real_stdout
    json.dump(result, sys.stdout)
    sys.stdout.flush()
    return 0 if result["status"] == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
