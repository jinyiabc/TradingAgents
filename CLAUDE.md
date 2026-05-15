# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common commands

```bash
# Install (editable for development)
pip install -e .

# Interactive CLI
tradingagents analyze                       # installed entry point (cli.main:app)
tradingagents analyze --checkpoint          # opt into LangGraph checkpoint/resume
tradingagents analyze --clear-checkpoints   # wipe ~/.tradingagents/cache/checkpoints/ first
python -m cli.main analyze                  # alternative when not pip-installed

# Run a single analysis from Python (see main.py for a full example)
python main.py

# Tests — pytest config in pyproject.toml; markers: unit, integration, smoke
python -m pytest tests/ -v
python -m pytest tests/test_structured_agents.py -v
python -m pytest -m unit                    # marker-scoped
python -m pytest tests/test_memory_log.py::test_idempotent_store_decision

# Diagnostic that exercises the three structured-output agents against a real LLM
OPENAI_API_KEY=... python scripts/smoke_structured_output.py openai
GOOGLE_API_KEY=... python scripts/smoke_structured_output.py google
ANTHROPIC_API_KEY=... python scripts/smoke_structured_output.py anthropic

# Docker
docker compose run --rm tradingagents
docker compose --profile ollama run --rm tradingagents-ollama
```

`tests/conftest.py` autouses a fixture that injects `placeholder` for all
provider API keys, so the suite runs cleanly without credentials. Real keys
are only needed for `scripts/smoke_structured_output.py` and any test that
hits a live provider.

## Architecture

### LangGraph pipeline (the big picture)

`TradingAgentsGraph` ([tradingagents/graph/trading_graph.py](tradingagents/graph/trading_graph.py)) is the orchestration entry
point. `propagate(ticker, date)` runs a fixed multi-agent workflow built in
[tradingagents/graph/setup.py](tradingagents/graph/setup.py):

```
START
  → selected analysts in sequence (market → social → news → fundamentals)
      each analyst loops with its ToolNode until ConditionalLogic stops it
  → Bull Researcher ⇄ Bear Researcher  (max_debate_rounds)
  → Research Manager   (deep-thinking LLM, structured output: ResearchPlan)
  → Trader             (quick LLM, structured output: TraderProposal)
  → Aggressive ⇄ Conservative ⇄ Neutral risk debaters  (max_risk_discuss_rounds)
  → Portfolio Manager  (deep-thinking LLM, structured output: PortfolioDecision)
  → END
```

Two LLM clients are created up-front: `quick_thinking_llm` (analysts,
researchers, trader, risk debaters, signal processor) and `deep_thinking_llm`
(Research Manager + Portfolio Manager). Provider-specific kwargs
(`google_thinking_level`, `openai_reasoning_effort`, `anthropic_effort`) are
forwarded through `_get_provider_kwargs`.

### LLM provider factory

[tradingagents/llm_clients/factory.py](tradingagents/llm_clients/factory.py) maps `llm_provider` to a `BaseLLMClient`
subclass. OpenAI, xAI, DeepSeek, Qwen (DashScope), GLM (Zhipu), Ollama, and
OpenRouter all share `OpenAIClient` (OpenAI-compatible chat completions);
Anthropic, Google, and Azure each have their own client. Provider modules
are imported lazily so test collection doesn't pull in unused SDKs.

`backend_url` defaults to `None` — each client falls back to its native
endpoint. Setting it leaks across providers (a concrete bug fixed pre-0.2.4
where the OpenAI URL flowed into the Gemini client). Set it only when you
genuinely need a proxy or a self-hosted endpoint, and bear in mind it
applies to whichever provider is selected.

`MODEL_OPTIONS` in [tradingagents/llm_clients/model_catalog.py](tradingagents/llm_clients/model_catalog.py) is the single
source of truth for both CLI dropdown options and `validators.py`
provider/model validation. Adding a model means editing the catalog, not the CLI.

### Structured-output decision agents

Research Manager, Trader, and Portfolio Manager use
`llm.with_structured_output(Schema)` on their primary call and return typed
Pydantic instances defined in [tradingagents/agents/schemas.py](tradingagents/agents/schemas.py)
(`ResearchPlan`, `TraderProposal`, `PortfolioDecision`). The accompanying
`render_*` helpers convert each Pydantic instance back to the markdown shape
the rest of the system already consumes — memory log, CLI display, saved
report files, and the `SignalProcessor` heuristic all read this rendered
markdown. **Don't change a section header in a render helper without
checking what reads it.**

OpenAI structured-output calls deliberately use
`method="function_calling"` instead of the Responses-API parse path to
avoid noisy `PydanticSerializationUnexpectedValue` warnings.

Rating scales are deliberately split: 5-tier
(Buy/Overweight/Hold/Underweight/Sell) for Research Manager, Portfolio
Manager, signal processor, and the memory log; 3-tier (Buy/Hold/Sell) for
the Trader, since transaction direction is naturally ternary.

### Tools and data vendors

Each analyst's `ToolNode` is wired in `_create_tool_nodes`. The actual tool
implementations under [tradingagents/agents/utils/](tradingagents/agents/utils/) (`core_stock_tools.py`,
`technical_indicators_tools.py`, `fundamental_data_tools.py`,
`news_data_tools.py`) dispatch to vendor-specific implementations in
[tradingagents/dataflows/](tradingagents/dataflows/) (`y_finance.py`, `alpha_vantage*.py`).

Vendor selection is two-tier in `DEFAULT_CONFIG`:
- `data_vendors[<category>]` — default for everything in that category
  (`core_stock_apis`, `technical_indicators`, `fundamental_data`, `news_data`)
- `tool_vendors[<tool_name>]` — per-tool override that wins over the category default

Backtest fetchers are date-fidelity-aware: when `curr_date` falls inside a
fetched window, look-ahead data must be sliced off (this was fixed in
0.2.3 — keep it that way).

### Configuration plumbing

`DEFAULT_CONFIG` lives in [tradingagents/default_config.py](tradingagents/default_config.py). All filesystem
state defaults under `~/.tradingagents/` and is overridable via env vars:
`TRADINGAGENTS_RESULTS_DIR`, `TRADINGAGENTS_CACHE_DIR`,
`TRADINGAGENTS_MEMORY_LOG_PATH`.

User configs are layered with `DEFAULT_CONFIG.copy()`, then passed to
`TradingAgentsGraph(config=...)`. The constructor calls `set_config()` in
[tradingagents/dataflows/config.py](tradingagents/dataflows/config.py), which is the **module-level singleton**
that the `dataflows/` tools read at call time via `get_config()`. Mutating
the dict you passed in after construction will not reach the tools — pass
the final config in.

### Persistence

Two independent systems, both rooted under `~/.tradingagents/`:

1. **Decision log** ([tradingagents/agents/utils/memory.py](tradingagents/agents/utils/memory.py)) — always on.
   Append-only markdown at `~/.tradingagents/memory/trading_memory.md` with
   `<!-- ENTRY_END -->` HTML-comment delimiters (LLM prose can't produce
   them, so they're a safe hard separator). On a same-ticker re-run,
   `_resolve_pending_entries` fetches realised return + alpha vs SPY,
   generates a one-paragraph reflection via `Reflector`, and batch-writes
   the resolved entries. Only the Portfolio Manager prompt receives memory
   context; only resolved entries are surfaced (so empty memory cannot
   produce fabricated lessons). `memory_log_max_entries` caps resolved
   entries; pending entries are never pruned.

2. **Checkpoint resume** ([tradingagents/graph/checkpointer.py](tradingagents/graph/checkpointer.py)) — opt-in
   via `config["checkpoint_enabled"]` or `--checkpoint`. Per-ticker SQLite
   DBs at `~/.tradingagents/cache/checkpoints/<TICKER>.db` so concurrent
   tickers don't contend on a single file. `thread_id = sha256(TICKER:date)[:16]`
   so the same ticker+date resumes; a different date starts fresh. The
   checkpoint is cleared on successful completion. The graph is recompiled
   with the saver inside `propagate()` and reverted on exit, so the
   compiled `self.graph` outside a checkpointed call is always the
   uncheckpointed version.

`FinancialSituationMemory` (the per-agent BM25 memory) and
`reflect_and_remember()` plumbing have been **removed** in 0.2.4 in favour
of the decision log — don't reintroduce per-agent memories.

### Ticker safety

`safe_ticker_component(ticker)` in [tradingagents/dataflows/utils.py](tradingagents/dataflows/utils.py) is the
allowlist gate before any ticker reaches a path component (results dir,
checkpoint DB filename). Exchange-qualified tickers (e.g. `7203.T`,
`BRK.B`, `.HK`, `.L`, `.TO`) are preserved end-to-end through prompts and
tool calls — see `build_instrument_context` in
[tradingagents/agents/utils/agent_utils.py](tradingagents/agents/utils/agent_utils.py).

## Conventions specific to this repo

- **Always pass `encoding="utf-8"`** to `open()` and equivalent file I/O.
  Windows defaults to cp1252 and will raise `UnicodeEncodeError` on agent
  output otherwise. The 0.2.2 attempt to set a process-level UTF-8 default
  did not actually take effect; the working pattern is per-call.
- **Internal debate stays in English.** `output_language` only affects
  user-facing surfaces (analysts and Portfolio Manager rendered output) via
  `get_language_instruction()`; researchers and risk debaters never receive
  the language instruction so reasoning quality is preserved.
- **Don't add a separate signal-extraction LLM call.** `SignalProcessor`
  reads the rating from the Portfolio Manager's rendered markdown via a
  deterministic heuristic (`parse_rating` in
  [tradingagents/agents/utils/rating.py](tradingagents/agents/utils/rating.py)). Re-introducing an LLM here is a
  regression.
- **CLI welcome screen fetches announcements** from
  `api.tauric.ai/v1/announcements` ([cli/announcements.py](cli/announcements.py)). Network failures
  there must not block the CLI.
