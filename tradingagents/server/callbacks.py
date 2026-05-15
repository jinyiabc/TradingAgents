"""LangGraph/LangChain callback handlers used by the web job runner.

Both handlers are duck-typed (no inheritance from BaseCallbackHandler) — the
runtime calls whatever methods we define, and avoiding the import keeps this
module light and free of langchain pinning.
"""

from __future__ import annotations

import logging
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


class NodeProgressCallback:
    """Writes the current LangGraph node name into the job's `current_step`
    column on each `on_chain_start`. The job-status page reads it on every
    3s poll to render live progress.
    """

    def __init__(self, db_path: Path, job_id: str) -> None:
        self._db_path = db_path
        self._job_id = job_id
        from tradingagents.server.db import set_current_step

        self._set = set_current_step

    def on_chain_start(self, serialized: dict[str, Any], *_args: Any, **kwargs: Any) -> None:
        meta = kwargs.get("metadata") or {}
        node = meta.get("langgraph_node")
        if not node:
            return
        try:
            self._set(self._db_path, self._job_id, str(node))
        except Exception:  # noqa: BLE001 — telemetry must never fail the run
            logger.debug("set_current_step failed", exc_info=True)


class TokenUsageCallback:
    """Accumulates per-model token usage across every LLM completion in an
    analysis. `.totals()` returns the aggregate `(prompt_tokens,
    completion_tokens, estimated_cost_usd_or_None)` for writing into the
    jobs DB.

    Cost is `None` if any used model is missing from the pricing table —
    we prefer "no estimate" over a misleadingly partial one.
    """

    def __init__(self) -> None:
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
        cost_value: float | None = (
            None if any_unknown or (total_in + total_out) == 0 else round(cost, 4)
        )
        return total_in, total_out, cost_value
