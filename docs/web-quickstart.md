# Web — quick run guide

Local two-process setup (FastAPI backend + Next.js dev server) for the
TradingAgents web frontend. See [azure-web-deployment.md](azure-web-deployment.md)
for the production Azure path.

## Prerequisites (one-time)

```bash
cd /mnt/d/work/TradingAgents

# Editable Python install with server extras
.venv/bin/pip install -e '.[server]'

# Node via nvm — already installed at ~/.nvm in this WSL
export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh"

# Frontend deps
cd web && npm install && cd ..

# Provider keys in .env at repo root (DEEPSEEK_API_KEY etc.)
# .env is auto-loaded by the server on startup.
```

## Start

Two terminals.

**Terminal A — backend:**

```bash
cd /mnt/d/work/TradingAgents
TRADINGAGENTS_CORS_ORIGINS=http://localhost:3000 \
  .venv/bin/tradingagents-server
```

**Terminal B — frontend:**

```bash
cd /mnt/d/work/TradingAgents/web
export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh"
npm run dev
```

Open <http://localhost:3000>.

## What each port is

| Port | What | Process |
|---|---|---|
| 3000 | Next.js UI (the browser hits this) | `next-server` |
| 8000 | FastAPI backend; React fetches from here | `uvicorn tradingagents.server.app` |

The browser only ever talks to `:3000`. JavaScript loaded from there makes cross-origin `fetch()` calls to `:8000`. **Do not load `http://localhost:8000` in the browser** — it's the API, not the UI.

## Why `TRADINGAGENTS_CORS_ORIGINS=http://localhost:3000`

Without that env var, the backend defaults to `allow_origins=["*"]`, which forces `allow_credentials=False`. The frontend sends `credentials: "include"` for the auth-cookie path (`/me`, `/.auth/...`), and browsers reject `*` + credentials together. Setting CORS to a specific origin auto-flips credentials on.

Equivalent for production: set it to `https://<your-static-web-app-hostname>`.

## Verify it's healthy

```bash
curl -fs http://localhost:8000/healthz       # → {"ok": true}
curl -fs http://localhost:8000/config/options | jq .providers   # → list of providers
curl -fs http://localhost:3000/               # → HTTP 200 (or 308 → /)
```

## Submit an analysis (without the UI)

```bash
JOB=$(curl -s -X POST http://localhost:8000/analyses \
  -H "Content-Type: application/json" \
  -d '{
    "ticker": "NVDA",
    "analysis_date": "2026-05-15",
    "analysts": ["market"],
    "llm_provider": "deepseek",
    "quick_thinking_model": "deepseek-chat",
    "deep_thinking_model": "deepseek-reasoner",
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    "output_language": "English"
  }' | jq -r .job_id)

watch -n 3 "curl -s http://localhost:8000/analyses/$JOB | jq '{status, current_step, prompt_tokens, estimated_cost_usd}'"
```

## Stop

```bash
# Backend
pkill -f 'uvicorn tradingagents'

# Frontend
pkill -f 'next-server'

# Or by port
fuser -k 8000/tcp
fuser -k 3000/tcp
```

## Common errors

### `Cannot find module './<N>.js'` in the browser

Happens when `npm run build` runs against the same `.next/` while `npm run dev` is alive — the production build overwrites the dev chunks. Always fixable with:

```bash
pkill -f 'next-server'
rm -rf /mnt/d/work/TradingAgents/web/.next
cd /mnt/d/work/TradingAgents/web && npm run dev
# Then hard-reload the browser (Ctrl+Shift+R)
```

### CORS error in the browser console

The backend isn't sending `Access-Control-Allow-Origin` for `http://localhost:3000`. Restart the backend with `TRADINGAGENTS_CORS_ORIGINS=http://localhost:3000`.

### Job fails immediately with `RuntimeError: <PROVIDER>_API_KEY is not set`

The provider you picked in the form needs that env var. Put it in `.env` at the repo root (it auto-loads) and restart the backend. Recognised keys: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `DEEPSEEK_API_KEY`, `XAI_API_KEY`, `DASHSCOPE_API_KEY`, `ZHIPU_API_KEY`, `OPENROUTER_API_KEY`.

### Job fails with `BadRequestError: insufficient tool messages...`

Known DeepSeek/LangChain handshake flake; the analysts auto-retry once on this. If it slips through, hit **Retry** on the job status page — there's no checkpoint to poison, each retry is a fresh roll.

### `YFRateLimitError: Too Many Requests`

Yahoo Finance's per-IP burst limit. Wait a couple of minutes and **Retry**, or set `TRADINGAGENTS_DATA_VENDOR=alpha_vantage` in the env (needs `ALPHAVANTAGE_API_KEY` in `.env`).

### Frontend can't reach backend

Check both processes are listening:

```bash
ss -tlnp 2>/dev/null | grep -E ":3000|:8000"
```

If only one or neither, restart it.

## Reset all state

```bash
rm -rf ~/.tradingagents/web/jobs.sqlite            # all job history
rm -rf ~/.tradingagents/cache/checkpoints/*.db     # checkpoint resume DBs
rm -rf ~/.tradingagents/logs/                      # generated analysis reports
rm -rf /mnt/d/work/TradingAgents/web/.next         # frontend build cache
```

## Convenience aliases

Add to `~/.bashrc`:

```bash
alias ta-server='cd /mnt/d/work/TradingAgents && TRADINGAGENTS_CORS_ORIGINS=http://localhost:3000 .venv/bin/tradingagents-server'
alias ta-web='cd /mnt/d/work/TradingAgents/web && export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh" && npm run dev'
```

Then `ta-server` in one terminal, `ta-web` in another.
