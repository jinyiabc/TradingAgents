# Upstream bug: DeepSeek + langchain-openai tool-call handshake malforms history

Draft of an issue to file against [langchain-ai/langchain](https://github.com/langchain-ai/langchain/issues) (or the `langchain-openai` package specifically). **Not yet filed** — needs a minimal self-contained reproducer before submitting; the symptoms below were observed only inside a full multi-agent LangGraph pipeline.

## Symptom

In a LangGraph workflow that uses `ChatOpenAI` (or a `ChatOpenAI` subclass) pointed at DeepSeek's API with `bind_tools(...)`, the analyst node's `chain.invoke(state["messages"])` intermittently returns successfully on the first call (assistant message with `tool_calls`), the ToolNode executes the tools and appends `ToolMessage`s, and then the **next** `chain.invoke` round-trips a 400 from DeepSeek:

```
openai.BadRequestError: Error code: 400 - {'error': {'message':
  "An assistant message with 'tool_calls' must be followed by tool messages
   responding to each 'tool_call_id'. (insufficient tool messages following
   tool_calls message)", ...}}
```

The error is **non-deterministic** — same inputs, same models, same code path: sometimes the analyst loop completes cleanly, sometimes it 400s on the second LLM round. Observed empirical failure rate ≈ 40–60 % of attempts on Market Analyst's first re-entry (measured across V3.2 `deepseek-chat` + `deepseek-reasoner` and V4 `deepseek-v4-flash` + `deepseek-v4-pro`).

The error sometimes manifests *instead* as a `YFRateLimitError` because langchain catches the 400 and internally retries the analyst, which re-fires its tool burst — and the burst trips Yahoo Finance's per-IP limit. Both errors trace back to the same handshake.

## Suspected mechanism (unconfirmed)

`DeepSeekChatOpenAI._get_request_payload` (or whatever overrides langchain-openai uses for DeepSeek's reasoning-content reattachment) appears to drop or re-key one of the `tool_call_id`s when rendering an in-flight assistant message back into the request payload. That breaks the invariant DeepSeek's API enforces — every `tool_call_id` in the assistant message must have a matching `ToolMessage` afterward.

We have not pinned down the exact code path. The reattachment is suspected because the failure rate appears higher with V4 thinking-mode models (which carry more `reasoning_content`) but it also fires with non-thinking V3.2 chat models, so it's not purely thinking-mode.

## Reproducibility budget

In the TradingAgents repo, the bug reproduces inside the Market Analyst node:

```
File "tradingagents/agents/analysts/market_analyst.py", line 76, in market_analyst_node
  result = chain.invoke(state["messages"])
```

after one round of tool-execution. A minimal repro outside the agent pipeline has not been constructed yet — that's the gating item for actually filing this.

## Workaround currently shipped in this repo

Commit [`74c118c`](../tradingagents/agents/utils/agent_utils.py) wraps `chain.invoke` in `invoke_with_tool_call_repair`, which on `BadRequestError` matching "insufficient tool messages":

1. Walks the history, finds AIMessages with `tool_calls` whose IDs don't all have matching ToolMessage responses
2. Drops those orphan tool_calls (preserves content)
3. Retries `chain.invoke` once on the sanitised history

This converts the deterministic-after-malformation 400 into a usable retry. Per-attempt success rate on the analysts went from ≈ 50 % to ≈ 95 % (sample size ~15 since the workaround landed).

## Versions

- `yfinance` 1.3.0
- `curl_cffi` 0.15.0
- `langchain-openai` (per the venv at the time of bug — needs to be re-pinned when filing)
- `langgraph` >= 0.4.8
- DeepSeek models: `deepseek-chat`, `deepseek-reasoner`, `deepseek-v4-flash`, `deepseek-v4-pro` (all affected at varying rates)

## TODO before filing

1. Build a minimal repro: ChatOpenAI(model=..., base_url=DeepSeek, api_key=...) + `bind_tools` on two trivial tools + a small loop that mimics `analyst → tool → analyst`. Run N times, count 400s.
2. Capture a full request/response pair where the 400 happens — particularly the assistant message body (with tool_calls) and the rendered request payload langchain-openai actually sent.
3. Confirm versions of `langchain-openai`, `langchain-core`, `openai`, and `langgraph` at repro time.
4. Search `langchain-ai/langchain` issues for any open ticket that already describes this — Agent C's pass didn't find one but the search wasn't exhaustive.
5. Decide whether to file under `langchain-openai` (the suspected source) or the top-level `langchain` repo.
