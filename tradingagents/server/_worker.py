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
import sys
import traceback
from pathlib import Path


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

        graph = TradingAgentsGraph(selected_analysts=analysts, config=config)
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
