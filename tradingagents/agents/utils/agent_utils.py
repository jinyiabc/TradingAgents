from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage

logger = logging.getLogger(__name__)

# Import tools from separate utility files
from tradingagents.agents.utils.core_stock_tools import (
    get_stock_data
)
from tradingagents.agents.utils.technical_indicators_tools import (
    get_indicators
)
from tradingagents.agents.utils.fundamental_data_tools import (
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement
)
from tradingagents.agents.utils.news_data_tools import (
    get_news,
    get_insider_transactions,
    get_global_news
)


def get_language_instruction() -> str:
    """Return a prompt instruction for the configured output language.

    Returns empty string when English (default), so no extra tokens are used.
    Only applied to user-facing agents (analysts, portfolio manager).
    Internal debate agents stay in English for reasoning quality.
    """
    from tradingagents.dataflows.config import get_config
    lang = get_config().get("output_language", "English")
    if lang.strip().lower() == "english":
        return ""
    return f" Write your entire response in {lang}."


def build_instrument_context(ticker: str) -> str:
    """Describe the exact instrument so agents preserve exchange-qualified tickers."""
    return (
        f"The instrument to analyze is `{ticker}`. "
        "Use this exact ticker in every tool call, report, and recommendation, "
        "preserving any exchange suffix (e.g. `.TO`, `.L`, `.HK`, `.T`)."
    )

def create_msg_delete():
    def delete_messages(state):
        """Clear messages and add placeholder for Anthropic compatibility"""
        messages = state["messages"]

        # Remove all messages
        removal_operations = [RemoveMessage(id=m.id) for m in messages]

        # Add a minimal placeholder message
        placeholder = HumanMessage(content="Continue")

        return {"messages": removal_operations + [placeholder]}

    return delete_messages


def _strip_dangling_tool_calls(messages: list) -> list:
    """Return a new message list where any AIMessage with tool_calls whose IDs
    don't all have matching ToolMessages downstream has those orphan tool_calls
    removed (content preserved).

    The DeepSeek tool-call handshake intermittently leaves the conversation in
    a state where an assistant message references tool_call_ids that never get
    answered. Sending that history back to DeepSeek triggers a 400 with
    "insufficient tool messages following tool_calls". Stripping the orphans
    lets the next LLM turn replay the work without rejecting the history.
    """
    tool_msg_ids = {
        m.tool_call_id for m in messages
        if isinstance(m, ToolMessage) and getattr(m, "tool_call_id", None)
    }
    repaired = []
    for m in messages:
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            kept = [tc for tc in m.tool_calls if tc.get("id") in tool_msg_ids]
            if len(kept) != len(m.tool_calls):
                # Pydantic-style copy that preserves content + metadata.
                m = m.model_copy(update={"tool_calls": kept})
        repaired.append(m)
    return repaired


def invoke_with_tool_call_repair(chain: Any, messages: list, *, max_retries: int = 1) -> Any:
    """Invoke a chain on a message history; on DeepSeek's "insufficient tool
    messages" 400, strip dangling tool_calls and retry.

    The bug is upstream in the DeepSeek + langchain-openai tool-call handshake
    (see docs/azure-web-deployment.md §11). Until that's solid, this guard
    converts a deterministic 400 on a malformed history into a successful
    retry on a sanitised one. Other API errors propagate.
    """
    # Lazy import so this module doesn't pull in `openai` at top-level.
    from openai import BadRequestError

    for attempt in range(max_retries + 1):
        try:
            return chain.invoke(messages)
        except BadRequestError as exc:
            text = str(exc).lower()
            if "insufficient tool messages" not in text and "tool_calls" not in text:
                raise
            if attempt >= max_retries:
                raise
            logger.warning(
                "DeepSeek 400 on tool_calls handshake; stripping orphans and retrying (%d/%d)",
                attempt + 1,
                max_retries,
            )
            messages = _strip_dangling_tool_calls(messages)


        
