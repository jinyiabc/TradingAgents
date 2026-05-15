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
from collections import defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# $/M tokens, (input, output). Approximate; for in-UI guidance, not billing.
# Keep small — providers we actually run; anything missing surfaces token
# counts with cost=None rather than blocking the run.
_PRICING_USD_PER_MILLION: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-5.4": (5.0, 15.0),
    "gpt-5.4-mini": (0.5, 1.5),
    "gpt-5.4-nano": (0.1, 0.3),
    "gpt-5.4-pro": (30.0, 180.0),
    "gpt-5.2": (3.0, 12.0),
    "gpt-4.1": (2.0, 8.0),
    # Anthropic
    "claude-opus-4-6": (15.0, 75.0),
    "claude-opus-4-5": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (0.8, 4.0),
    # Google
    "gemini-3.1-pro-preview": (3.5, 14.0),
    "gemini-3-flash-preview": (0.3, 2.5),
    "gemini-2.5-pro": (1.25, 5.0),
    "gemini-2.5-flash": (0.075, 0.3),
    # DeepSeek
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.20),
    "deepseek-v4-flash": (0.15, 0.60),
    "deepseek-v4-pro": (0.55, 2.20),
}


class _TokenUsageCallback:
    """Duck-typed LangChain callback that accumulates per-model token usage
    across every LLM completion in an analysis. `.totals()` returns the
    aggregate (input, output, cost_usd_or_None) for writing into jobs DB.
    """

    def __init__(self) -> None:
        # model_name -> [input_tokens, output_tokens]
        self._usage: dict[str, list[int]] = defaultdict(lambda: [0, 0])

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        try:
            output = getattr(response, "llm_output", None) or {}
            model = output.get("model_name") or output.get("model")
            usage = output.get("token_usage") or output.get("usage")

            # Newer LangChain attaches usage on each generation's AIMessage.
            if not usage:
                for gen_group in getattr(response, "generations", None) or []:
                    for gen in gen_group:
                        msg = getattr(gen, "message", None)
                        meta = getattr(msg, "usage_metadata", None) if msg else None
                        if meta:
                            usage = {
                                "prompt_tokens": meta.get("input_tokens"),
                                "completion_tokens": meta.get("output_tokens"),
                            }
                            if not model:
                                model = getattr(msg, "response_metadata", {}).get(
                                    "model_name"
                                )
                            break
                    if usage:
                        break

            if not usage:
                return
            in_ = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            out_ = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
            key = (model or "unknown").lower()
            self._usage[key][0] += in_
            self._usage[key][1] += out_
        except Exception:  # noqa: BLE001 — telemetry must never break a run
            logger.debug("on_llm_end failed", exc_info=True)

    def totals(self) -> tuple[int, int, float | None]:
        """Return (prompt_tokens, completion_tokens, estimated_cost_usd_or_None).

        Cost is None if any used model is missing from the pricing table — we
        prefer "no estimate" over a misleadingly partial one.
        """
        total_in = 0
        total_out = 0
        cost = 0.0
        any_unknown = False
        for model, (in_, out_) in self._usage.items():
            total_in += in_
            total_out += out_
            prices = _PRICING_USD_PER_MILLION.get(model)
            if prices is None:
                any_unknown = True
                continue
            cost += in_ * prices[0] / 1_000_000 + out_ * prices[1] / 1_000_000
        cost_value: float | None = None if any_unknown or (total_in + total_out) == 0 else round(cost, 4)
        return total_in, total_out, cost_value


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
    token_cb = _TokenUsageCallback()
    try:
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        from tradingagents.report_writer import save_report_to_disk

        cbs: list[Any] = [token_cb]
        if db_path and job_id:
            cbs.append(_NodeProgressCallback(Path(db_path), job_id))

        graph = TradingAgentsGraph(
            selected_analysts=analysts,
            config=config,
            graph_callbacks=cbs,
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

    # Record telemetry whether the run succeeded or failed; even a partial run
    # used tokens worth surfacing in the UI.
    if db_path and job_id:
        try:
            from tradingagents.server.db import set_telemetry

            in_, out_, cost = token_cb.totals()
            set_telemetry(
                Path(db_path),
                job_id,
                prompt_tokens=in_ or None,
                completion_tokens=out_ or None,
                estimated_cost_usd=cost,
            )
        except Exception:  # noqa: BLE001 — telemetry never blocks the result
            logger.debug("set_telemetry failed", exc_info=True)

    sys.stdout = real_stdout
    json.dump(result, sys.stdout)
    sys.stdout.flush()
    return 0 if result["status"] == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
