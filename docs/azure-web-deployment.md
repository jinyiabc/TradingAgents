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
5. **Checkpointing is currently disabled** (`config["checkpoint_enabled"] = False`, see [jobs.py](../tradingagents/server/jobs.py) `_build_config`). Originally on for crash-recovery, but the LangChain/DeepSeek tool-call handshake fails on Market Analyst's first turn often enough that LangGraph regularly persists an assistant message with `tool_calls` whose tool responses didn't all land. Resuming from that poisoned state 400s deterministically on DeepSeek's "insufficient tool messages" check, converting a single transient flake into an unrecoverable loop. Until the upstream handshake is fixed, each run is independent; a crashed analysis is lost and resubmitted from scratch.

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

4. **M4 — Deploy backend** *(workflow written; first deploy is owner-triggered)*
   - [`.github/workflows/deploy-server.yml`](../.github/workflows/deploy-server.yml) runs on every push to `main` that touches the backend (or via manual dispatch). Stages:
     1. `test` — installs the package and runs `pytest`.
     2. `build-and-push` — multi-platform Docker build of the `server` target in [`Dockerfile`](../Dockerfile), pushes to `ghcr.io/<owner>/tradingagents-server:latest` and `:<sha12>`.
     3. `deploy` — `azure/login@v2` via OIDC federated identity, then `az containerapp update --image ...` to roll a new revision.
   - **One-time Azure OIDC setup** (do this once before triggering the workflow):
     ```bash
     # 1. Create the app registration that GitHub will federate as.
     az ad app create --display-name tradingagents-deploy
     APP_ID=$(az ad app list --display-name tradingagents-deploy --query '[0].appId' -o tsv)
     az ad sp create --id $APP_ID

     # 2. Federated credential for github.com/<owner>/<repo> on main branch.
     az ad app federated-credential create --id $APP_ID --parameters '{
       "name": "tradingagents-main",
       "issuer": "https://token.actions.githubusercontent.com",
       "subject": "repo:<owner>/<repo>:ref:refs/heads/main",
       "audiences": ["api://AzureADTokenExchange"]
     }'

     # 3. Grant the service principal Contributor on the resource group.
     RG_ID=$(az group show -n tradingagents-prod-rg --query id -o tsv)
     az role assignment create --assignee $APP_ID --role Contributor --scope $RG_ID
     ```
     Then set these **repository secrets** in GitHub (Settings → Secrets and variables → Actions):
     - `AZURE_CLIENT_ID` — the `$APP_ID` from above
     - `AZURE_TENANT_ID` — `az account show --query tenantId -o tsv`
     - `AZURE_SUBSCRIPTION_ID` — `az account show --query id -o tsv`
   - First deploy: push to `main` (or use *Actions → deploy-server → Run workflow*). The image gets built, pushed to GHCR, and the Container App revision is replaced. Verify with `curl https://<backendFqdn>/healthz`.
   - GHCR images are private by default; either keep them so (the Container App pulls via its system-assigned identity once you grant it `acrpull`-equivalent permission on the GHCR package), or flip the package visibility to public in GitHub for the simpler path.

5. **M5 — Deploy frontend** *(workflow + static-export config done)*
   - [`.github/workflows/deploy-web.yml`](../.github/workflows/deploy-web.yml) builds the Next.js app and pushes `web/out/` to the Static Web App via `Azure/static-web-apps-deploy@v1`.
   - Required for static export: dynamic routes were refactored to query strings (`/jobs?id=…`, `/jobs/report?id=…`) so the whole frontend pre-renders. [`web/next.config.mjs`](../web/next.config.mjs) sets `output: "export"` and `trailingSlash: true`.
   - **One-time setup**:
     ```bash
     # Fetch the SWA deployment token after Bicep creates the resource.
     TOKEN=$(az staticwebapp secrets list \
       --name tradingagents-prod-web \
       --resource-group tradingagents-prod-rg \
       --query properties.apiKey -o tsv)
     ```
     Then add the repo secret `AZURE_STATIC_WEB_APPS_API_TOKEN` (Settings → Secrets and variables → Actions) and the repo variable `NEXT_PUBLIC_API_BASE` (e.g. `https://tradingagents-prod-api.<region>.azurecontainerapps.io`).
   - First deploy: push to `main` (or *Run workflow*); the SWA action uploads the static bundle.

6. **M6 — Auth** *(done, deploy-gated)*
   - Container Apps Easy Auth ([`infra/modules/container-app-auth.bicep`](../infra/modules/container-app-auth.bicep)) federates with Azure AD. Off by default — flip `enableAuth=true` plus the AAD params at deploy time.
   - `excludedPaths` keeps `/healthz` reachable for Azure probes; everything else returns 401 to unauthenticated XHR (frontend then redirects to `/.auth/login/aad`).
   - Backend exposes `/me` ([`tradingagents/server/app.py`](../tradingagents/server/app.py)) which decodes the `X-MS-CLIENT-PRINCIPAL` header Easy Auth injects. CORS auto-flips to `allow_credentials=True` when the configured origins list is non-wildcard.
   - Frontend ([`web/lib/api.ts`](../web/lib/api.ts)) sends `credentials: "include"` on every fetch; the nav shows the signed-in user via [`web/components/UserBadge.tsx`](../web/components/UserBadge.tsx) and offers a `Sign out` link to `/.auth/logout`.
   - **One-time AAD setup**:
     ```bash
     # Create the app registration.
     az ad app create \
       --display-name tradingagents-easyauth \
       --web-redirect-uris "https://<backend-fqdn>/.auth/login/aad/callback"
     APP_ID=$(az ad app list --display-name tradingagents-easyauth --query '[0].appId' -o tsv)
     az ad sp create --id $APP_ID

     # Generate a client secret valid for 1 year.
     SECRET=$(az ad app credential reset --id $APP_ID --years 1 --query password -o tsv)

     # Get tenant ID.
     TENANT=$(az account show --query tenantId -o tsv)

     # Re-deploy with auth enabled, passing the secret as a parameter.
     az deployment sub create \
       --location eastus \
       --template-file infra/main.bicep \
       --parameters infra/main.parameters.json \
       --parameters enableAuth=true \
                    aadClientId=$APP_ID \
                    aadClientSecret=$SECRET \
                    aadTenantId=$TENANT
     ```
     Restrict who can sign in by setting `aadAllowedAudience` to a comma-separated list, or use the AAD app reg's *Enterprise Application → Properties → Assignment required* setting plus user/group assignments.
   - Also set the backend's CORS origins so `allow_credentials` engages:
     ```bash
     az containerapp update --name tradingagents-prod-api \
       --resource-group tradingagents-prod-rg \
       --set-env-vars TRADINGAGENTS_CORS_ORIGINS=https://<static-web-app-hostname>
     ```

7. **M7 — Polish** *(in progress)*
   - ✓ **Retry button on failed jobs** ([`web/app/jobs/page.tsx`](../web/app/jobs/page.tsx)) — re-POSTs the saved request config and redirects to the new `job_id`. Most relevant for the residual ~5–10% per-attempt failure rate from the LangChain/DeepSeek tool-call handshake.
   - ✓ **Friendlier error display** — exception type + message rendered prominently; the full traceback hides behind a `<details>` toggle.
   - ✓ **History page filters** ([`web/app/history/page.tsx`](../web/app/history/page.tsx)) — ticker, status, and analysis-date-range, applied client-side. Shows `N of M` count when any filter is active.
   - Remaining: cost/run telemetry surfaced in the UI, per-step pipeline progress.

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
