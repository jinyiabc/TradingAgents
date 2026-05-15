# Azure Web Deployment — Design

Status: **draft for review**
Owner: jinyiabc
Last updated: 2026-05-15

## 1. Goals

- Run TradingAgents in Azure with a browser frontend, in addition to the existing CLI.
- Single-tenant: just the owner and a small invited group share one deployment.
- Server-side provider keys (one set), no per-user key entry.
- Submit an analysis from the browser, watch periodic progress, view the consolidated HTML report when done.
- Reuse [`TradingAgentsGraph.propagate()`](../tradingagents/graph/trading_graph.py) and its existing on-disk artefacts unchanged.

## 2. Non-goals (v1)

- Multi-tenant sign-up / per-user billing.
- Streaming the agent-by-agent debate to the browser in real time (polling is enough for v1; can be added later via SSE without re-architecting).
- Replacing the CLI. The CLI ships as-is.
- Editing or replaying past analyses from the UI (read-only history only).

## 3. Architecture

```
┌────────────────────────────┐                ┌─────────────────────────────┐
│   Next.js (Static Web App) │  HTTPS / JSON  │  FastAPI (Container App)    │
│                            │ ─────────────► │                             │
│  • New Analysis form       │  ◄──────────── │  • POST /analyses           │
│  • Job Status (polls 3s)   │                │  • GET  /analyses/{id}      │
│  • Report Viewer (HTML)    │                │  • GET  /analyses/{id}/report│
│  • History list            │                │  • GET  /analyses           │
└────────────────────────────┘                │                             │
                                              │  background runner:         │
                                              │  TradingAgentsGraph         │
                                              │    .propagate(ticker,date)  │
                                              └─────────────┬───────────────┘
                                                            │
                                                            ▼
                                              ┌─────────────────────────────┐
                                              │  Azure Files share          │
                                              │  mounted at ~/.tradingagents│
                                              │                             │
                                              │  • results/<TKR>/<date>/    │
                                              │      complete_report.html   │
                                              │      complete_report.md     │
                                              │  • cache/checkpoints/*.db   │
                                              │  • memory/trading_memory.md │
                                              │  • web/jobs.sqlite          │
                                              └─────────────────────────────┘

  Container App env vars (secrets):
  OPENAI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_API_KEY, …
```

Auth via Container Apps built-in Easy Auth (Azure AD) — no app code, just config. Static Web App proxies to the Container App over the private link so the API is not directly exposed.

## 4. Backend (FastAPI)

New package: `tradingagents/server/`. Existing CLI in `cli/` untouched.

### 4.1 API surface

| Method | Path | Body / Query | Returns |
|---|---|---|---|
| POST | `/analyses` | `{ ticker, date, analysts[], llm_provider, models, max_debate_rounds, max_risk_discuss_rounds, output_language }` | `{ job_id }` |
| GET | `/analyses` | `?limit=50` | `[{ job_id, ticker, date, status, created_at, finished_at }]` |
| GET | `/analyses/{job_id}` | — | `{ job_id, status, current_step, progress, error, report_url }` |
| GET | `/analyses/{job_id}/report` | — | `text/html` (served from Azure Files) |
| GET | `/healthz` | — | `200 OK` (used by Container App probes) |

`status` ∈ `queued | running | done | failed | cancelled`.
`current_step` is the LangGraph node name (e.g. `"Bear Researcher"`, `"Portfolio Manager"`). Updated after each node completes.

### 4.2 Job lifecycle

1. `POST /analyses` validates input (reuses [`validators.py`](../tradingagents/llm_clients/validators.py)), generates `job_id = uuid4()`, inserts row in `jobs.sqlite` with status `queued`, schedules an asyncio task, returns the id.
2. Background runner acquires the concurrency semaphore (cap = 2 by default), updates status to `running`, then iterates `graph.stream(...)` instead of `propagate(...)`. After each yielded node, it writes the node name and updated state-progress to `jobs.sqlite`.
3. On normal completion: status → `done`, `report_url` set to `/analyses/{id}/report`.
4. On exception: status → `failed`, `error` captures message + truncated traceback.
5. **Checkpointing is forced on for the web service** (`config["checkpoint_enabled"] = True`) so a container restart mid-run resumes from the last node when the job is retried. (Auto-retry is out of scope for v1; we expose a "retry" button in the UI.)

### 4.3 Concurrency

- `asyncio.Semaphore(max_concurrent_jobs)` — default 2.
- Single-process: jobs run inside the FastAPI worker. No Celery, no Redis.
- Container App `minReplicas=0, maxReplicas=1`. Scale-to-zero is fine because cold start + analysis startup is dwarfed by analysis duration. We don't scale to N because multiple replicas would race on `jobs.sqlite` and on the checkpoint DBs.

### 4.4 Why not a separate worker tier?

- Adds a queue, a worker pool, IPC, retry semantics. For ≤ a few analyses per day from a small group, an in-process semaphore is the right granularity.
- If we ever need > 1 replica or background retries: swap `jobs.sqlite` for Azure Service Bus + a worker Container App. The API surface doesn't change.

## 5. Frontend (Next.js on Static Web Apps)

Pages:

- `/` — **New Analysis form.** Dropdowns hydrate from `GET /config/options` (a new endpoint that exposes [`MODEL_OPTIONS`](../tradingagents/llm_clients/model_catalog.py)). Submits to `POST /analyses`, redirects to `/jobs/{id}`.
- `/jobs/{id}` — **Job Status.** Polls `GET /analyses/{id}` every 3s. Shows ticker, date, status badge, "current step" with a small step list (analysts → researchers → research mgr → trader → risk debate → portfolio mgr), elapsed time. When `done`, embeds the report in an iframe.
- `/jobs/{id}/report` — **Report Viewer.** Renders the HTML report full-page.
- `/history` — table of recent analyses, filterable by ticker, links to each report.

Why Next.js + Static Web Apps: SSR isn't strictly needed but Static Web Apps' built-in routing, GitHub Actions integration, and Azure AD auth integration make it the path of least friction. Plain Vite + React is fine too if SSR feels like overkill.

## 6. Persistence

| What | Where | Notes |
|---|---|---|
| Analysis results (md + html + section files) | `~/.tradingagents/results/<TICKER>/<date>/` on Azure Files | Existing code path, no changes. |
| Checkpoint DBs | `~/.tradingagents/cache/checkpoints/<TICKER>.db` on Azure Files | Existing code path. |
| Decision log | `~/.tradingagents/memory/trading_memory.md` on Azure Files | Existing code path. |
| Web jobs table | `~/.tradingagents/web/jobs.sqlite` on Azure Files | New; only the web service reads/writes. |

Single Azure Files share, single mount point. The whole `~/.tradingagents/` tree is one persistent volume.

## 7. Auth

Easy Auth on the Container App + Azure AD app registration. Allowed users listed by email. Frontend pulls the JWT from `/.auth/me`; backend trusts the `X-MS-CLIENT-PRINCIPAL` header.

For an absolute MVP (just the owner, no invited users yet) we can skip auth entirely and gate the Container App behind a private VNet ingress. Either is fine; pick when we cut the resource group.

## 8. Provider keys

Container App secrets, surfaced as env vars: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `XAI_API_KEY`, `DEEPSEEK_API_KEY`, `DASHSCOPE_API_KEY`, `ZHIPU_API_KEY`, `OPENROUTER_API_KEY`, `AZURE_OPENAI_*`.

Rotation: edit the secret in Container App, restart. (Acknowledged: this is less ergonomic than Key Vault; v1 chooses simplicity.)

## 9. CI/CD

- **GitHub Actions** on push to `main`:
  1. Run `pytest`.
  2. Build backend image, push to registry.
  3. `az containerapp update --image ...` to roll the new revision.
  4. Build & deploy frontend via the Static Web Apps action.
- **Registry choice:** GitHub Container Registry (free) unless we want to keep image storage inside Azure (ACR Basic ≈ $5/mo). Default to GHCR.

## 10. Cost estimate (rough, USD)

Idle, no analyses running:
- Container App (scale-to-zero): ~$0
- Static Web App: free tier
- Storage Account (Files share, < 1 GB): ~$0.10
- Log Analytics (minimal retention): ~$0
- **Total fixed: < $1/mo**

Per active analysis:
- Container App compute: a 5-minute run at 1 vCPU / 2 GB ≈ $0.02 of compute
- LLM costs dominate everything else and are unchanged from CLI usage.

Multi-replica or heavy concurrent use changes this picture; v1 stays single-replica.

## 11. Build order (milestones)

Each milestone is a working, demoable state.

1. **M1 — Backend skeleton (local)**
   - `tradingagents/server/` package: FastAPI app, `jobs.sqlite` schema, job runner wrapping `graph.stream(...)`.
   - API endpoints listed in §4.1. Run with `uvicorn`. Verify locally that `POST /analyses` produces a `complete_report.html` and `GET /analyses/{id}` reflects progress.
   - Dockerfile target `server` (multi-stage with the existing CLI image).

2. **M2 — Frontend skeleton (local)**
   - Next.js app under `web/`. Four pages from §5. Hardcode `NEXT_PUBLIC_API_BASE` to `http://localhost:8000`.
   - Manually run an end-to-end analysis through the UI against the local backend.

3. **M3 — Azure infra (greenfield)** *(done)*
   - Bicep under [`infra/`](../infra/): subscription-scope [`main.bicep`](../infra/main.bicep) creates the resource group; [`resources.bicep`](../infra/resources.bicep) orchestrates the modules — Log Analytics, Storage Account + Files share, Container Apps Environment (with the file-share storage attached), Container App for the FastAPI backend (volume-mounted at `/home/appuser/.tradingagents`, scale-to-zero, system-assigned identity, `/healthz` probes), and a Static Web App for the frontend.
   - Deploy:
     ```bash
     cp infra/main.parameters.example.json infra/main.parameters.json
     # edit the file: set provider API keys (or leave blank and add later)
     az login
     az deployment sub create \
       --location eastus \
       --template-file infra/main.bicep \
       --parameters infra/main.parameters.json
     ```
   - Outputs: `backendFqdn`, `staticWebAppHostname`, `storageAccountName`.
   - Nothing is exercised yet (no image pushed) — the backend will sit at scale-to-zero with `minReplicas=0` until M4 pushes the first revision.

4. **M4 — Deploy backend**
   - GitHub Actions workflow: build server image, push to GHCR, update Container App. Mount Azure Files at `/root/.tradingagents`. Secrets wired to env vars.
   - Hit the deployed `/healthz`; run one analysis via `curl`.

5. **M5 — Deploy frontend**
   - Static Web App Action wired to `web/`. Configure `API_BASE` to the Container App FQDN.
   - End-to-end run through the deployed UI.

6. **M6 — Auth**
   - Easy Auth on the Container App + Azure AD app reg.
   - Frontend reads `/.auth/me` for identity.

7. **M7 — Polish**
   - History page filters, retry button on failed jobs, friendlier error display, cost/run telemetry surfaced in the UI.

## 12. Open questions

- **Bicep or Terraform?** Bicep is lighter for greenfield Azure-only; Terraform is more portable. Default: Bicep.
- **Image registry: GHCR or ACR?** Default: GHCR (free).
- **Auth in M1 or skip until M6?** Default: skip; keep the Container App on a private ingress until M6.
- **Cancellation:** v1 has no "cancel running job" button. LangGraph doesn't expose a clean cancel hook mid-node, so it would require killing the asyncio task and tolerating partial state. Defer to v2 if needed.
- **Concurrency cap (current default 2):** revisit after first real usage; LLM rate limits may force this down.

## 13. Not in this design

Things we explicitly chose not to do in v1, captured so reviewers don't expect them:

- Per-user provider keys.
- Real-time streaming of agent reasoning to the browser.
- Multi-replica backend.
- Server-side scheduling of recurring analyses.
- Webhook / email notifications when an analysis completes.
