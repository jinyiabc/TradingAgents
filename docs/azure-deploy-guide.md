# Azure — step-by-step deployment guide

Greenfield deployment of the TradingAgents web stack to Azure. Companion
to [`azure-web-deployment.md`](azure-web-deployment.md) (which holds the
architectural rationale) and [`web-quickstart.md`](web-quickstart.md)
(local dev). Roughly **20–30 minutes** of work; first month idle cost
**< $1** (scale-to-zero Container App + Static Web App Free tier).

## What gets created

| Resource | Purpose |
|---|---|
| Resource group `tradingagents-prod-rg` | Container for everything below |
| Log Analytics workspace | Container Apps log destination |
| Storage account + Files share | Mounted at `/home/appuser/.tradingagents` so reports / jobs DB / checkpoints survive container restarts |
| Container Apps Environment | Workload profile = Consumption |
| **Container App** (`tradingagents-prod-api`) | FastAPI backend; scale-to-zero, `/healthz` probes |
| **Static Web App** (`tradingagents-prod-web`) | Hosts the Next.js bundle |

Two GitHub Actions workflows ([deploy-server.yml](../.github/workflows/deploy-server.yml), [deploy-web.yml](../.github/workflows/deploy-web.yml)) ship code into them on every push to `main`.

## Prereqs

```bash
az login                                # whichever subscription you want to use
az account show --query '{sub:id, tenant:tenantId}' -o table
az account set --subscription <SUB_ID>  # if needed
```

You also need the repo pushed to GitHub (it already is at `github.com/jinyiabc/TradingAgents`). The CI workflows live on `main`.

## 1. Create the AAD service principal for GitHub OIDC

This lets GitHub Actions deploy to Azure without long-lived secrets.

```bash
APP_NAME=tradingagents-deploy
REPO=jinyiabc/TradingAgents        # GitHub <owner>/<repo>

# 1a. App registration + service principal
az ad app create --display-name $APP_NAME
APP_ID=$(az ad app list --display-name $APP_NAME --query '[0].appId' -o tsv)
az ad sp create --id $APP_ID

# 1b. Federated credential that trusts GitHub Actions on main
az ad app federated-credential create --id $APP_ID --parameters "{
  \"name\": \"tradingagents-main\",
  \"issuer\": \"https://token.actions.githubusercontent.com\",
  \"subject\": \"repo:$REPO:ref:refs/heads/main\",
  \"audiences\": [\"api://AzureADTokenExchange\"]
}"

# 1c. Subscription-scope Contributor so it can both create the RG (via
#     Bicep) and update the Container App later. If you'd rather scope it
#     tighter, pre-create the RG and grant Contributor only there.
SUB_ID=$(az account show --query id -o tsv)
az role assignment create --assignee $APP_ID --role Contributor \
  --scope "/subscriptions/$SUB_ID"
```

Take note of:

```bash
echo "AZURE_CLIENT_ID:       $APP_ID"
echo "AZURE_TENANT_ID:       $(az account show --query tenantId -o tsv)"
echo "AZURE_SUBSCRIPTION_ID: $SUB_ID"
```

## 2. Deploy the infrastructure (Bicep)

```bash
cd /mnt/d/work/TradingAgents

# 2a. Parameters file. Leave provider API keys blank; set them in step 5.
cp infra/main.parameters.example.json infra/main.parameters.json
# Optional: edit infra/main.parameters.json — region, name prefix,
# min/max replicas, etc. Defaults are sensible.

# 2b. Subscription-scope deploy (creates the resource group)
az deployment sub create \
  --location eastus \
  --template-file infra/main.bicep \
  --parameters infra/main.parameters.json \
  --query 'properties.outputs'
```

Outputs come back as JSON — save them, you'll need them in step 3 / 4:

```json
{
  "backendFqdn":          { "value": "tradingagents-prod-api.<region>.azurecontainerapps.io" },
  "staticWebAppHostname": { "value": "<name>.<region>.azurestaticapps.net" },
  "storageAccountName":   { "value": "ta..." },
  "resourceGroupName":    { "value": "tradingagents-prod-rg" }
}
```

The Container App is up but pointing at the placeholder image `ghcr.io/jinyiabc/tradingagents-server:latest` with `minReplicas=0` — it has no replicas yet. That's intentional; the first CI run pushes the real image.

## 3. Get the Static Web App deployment token

```bash
SWA_TOKEN=$(az staticwebapp secrets list \
  --name tradingagents-prod-web \
  --resource-group tradingagents-prod-rg \
  --query properties.apiKey -o tsv)
echo "AZURE_STATIC_WEB_APPS_API_TOKEN: $SWA_TOKEN"
```

## 4. Configure GitHub secrets + variables

In the repo on github.com → Settings → Secrets and variables → Actions.

**Repository secrets** (Settings → Secrets and variables → Actions → New repository secret):

| Name | Value |
|---|---|
| `AZURE_CLIENT_ID` | `$APP_ID` from step 1 |
| `AZURE_TENANT_ID` | from step 1 |
| `AZURE_SUBSCRIPTION_ID` | from step 1 |
| `AZURE_STATIC_WEB_APPS_API_TOKEN` | `$SWA_TOKEN` from step 3 |

**Repository variable** (same screen → Variables tab):

| Name | Value |
|---|---|
| `NEXT_PUBLIC_API_BASE` | `https://<backendFqdn-from-step-2>` |

That last one is what tells the Next.js bundle where to call the API.

## 5. Set provider API keys on the Container App

The Container App was created with empty placeholder secrets. Fill in the ones you actually use (DeepSeek + OpenAI shown; same pattern for the others):

```bash
az containerapp secret set \
  --name tradingagents-prod-api \
  --resource-group tradingagents-prod-rg \
  --secrets \
    openai-api-key=sk-... \
    deepseek-api-key=sk-... \
    anthropic-api-key=sk-ant-... \
    google-api-key=...
```

The Bicep template already mapped these secret names to environment variables (`OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, etc.) on the container, so no second step is needed — restart the next time a new revision rolls.

## 6. CORS — point the backend at the Static Web App

```bash
FRONTEND_HOST=$(az staticwebapp show \
  --name tradingagents-prod-web \
  --resource-group tradingagents-prod-rg \
  --query defaultHostname -o tsv)

az containerapp update \
  --name tradingagents-prod-api \
  --resource-group tradingagents-prod-rg \
  --set-env-vars TRADINGAGENTS_CORS_ORIGINS=https://$FRONTEND_HOST
```

This flips `allow_credentials=True` on the backend (required for the
`/me` auth-cookie path; same reason as local dev needs it).

## 7. First CI deploy

Trigger both workflows once to actually ship code into the resources.

```bash
# Backend (pushes image to ghcr.io/jinyiabc/tradingagents-server, updates Container App)
gh workflow run deploy-server.yml --ref main

# Frontend (builds web/out and uploads to the Static Web App)
gh workflow run deploy-web.yml --ref main

# Watch
gh run list --workflow=deploy-server.yml --limit 1
gh run watch
```

After both succeed:

```bash
# Verify backend
curl -fsS "https://$FRONTEND_HOST/api/healthz" 2>&1 || \
curl -fsS "https://$(az containerapp show \
  --name tradingagents-prod-api --resource-group tradingagents-prod-rg \
  --query properties.configuration.ingress.fqdn -o tsv)/healthz"
```

## 8. Open the app

```bash
echo "https://$FRONTEND_HOST"
```

Open that in a browser. The New Analysis form will hydrate from `/config/options`. Submit an analysis; polling kicks in and the report renders when done — same UX as local.

## 9. (Optional) Enable Easy Auth (M6)

Locks the deployed app behind Azure AD sign-in. Off by default.

```bash
APP_ID_AUTH=$(az ad app create --display-name tradingagents-easyauth \
  --web-redirect-uris "https://$(az containerapp show \
    --name tradingagents-prod-api --resource-group tradingagents-prod-rg \
    --query properties.configuration.ingress.fqdn -o tsv)/.auth/login/aad/callback" \
  --query appId -o tsv)
az ad sp create --id $APP_ID_AUTH
SECRET=$(az ad app credential reset --id $APP_ID_AUTH --years 1 --query password -o tsv)
TENANT=$(az account show --query tenantId -o tsv)

az deployment sub create \
  --location eastus \
  --template-file infra/main.bicep \
  --parameters infra/main.parameters.json \
  --parameters \
      enableAuth=true \
      aadClientId=$APP_ID_AUTH \
      aadClientSecret=$SECRET \
      aadTenantId=$TENANT
```

After this, the Container App returns 401 for unauthenticated XHR; the
frontend's `redirectToLogin` (in [web/lib/api.ts](../web/lib/api.ts)) bounces
the user through `/.auth/login/aad` on the Container App, which then
redirects back to the SWA. The nav shows the signed-in user via
[UserBadge.tsx](../web/components/UserBadge.tsx).

## Day-2 operations

### Push code → it auto-deploys

Both workflows are path-filtered:

- Edits under `tradingagents/`, `cli/`, `Dockerfile`, `pyproject.toml` → triggers `deploy-server.yml`
- Edits under `web/` → triggers `deploy-web.yml`

A push to `main` runs whichever applies. You can also trigger manually from the Actions tab.

### Tail backend logs

```bash
az containerapp logs show \
  --name tradingagents-prod-api \
  --resource-group tradingagents-prod-rg \
  --tail 200 --follow
```

### Inspect Azure Files state

```bash
ACCT=$(az deployment sub show --name resources-prod \
  --query 'properties.outputs.storageAccountName.value' -o tsv)
KEY=$(az storage account keys list --account-name $ACCT --query '[0].value' -o tsv)
az storage file list --share-name tradingagents-data \
  --account-name $ACCT --account-key $KEY -o table
```

### Tear it down

```bash
az group delete --name tradingagents-prod-rg --yes --no-wait
```

That deletes everything — Container App, SWA, Storage (and all the analysis history in Azure Files). The AAD app registration from step 1 is in the tenant, not the RG; delete it separately if you want:

```bash
az ad app delete --id $APP_ID
```

## Rough monthly cost

| Resource | Idle | Active |
|---|---|---|
| Container App (scale-to-zero) | $0 | ~$0.02 per 5-min analysis |
| Static Web App | Free tier | Free tier |
| Storage (Files share, &lt; 1 GB) | $0.10 | $0.10 |
| Log Analytics (minimal retention) | ~$0 | ~$0 |
| **Total fixed** | **< $1/mo** | — |

LLM provider costs (DeepSeek / OpenAI / etc.) dominate everything else
and are unchanged from local usage. The cost-telemetry card on the job
status page surfaces the per-run estimate.
