// Subscription-scope entry point. Creates the resource group, then deploys
// everything else into it via resources.bicep.
//
// Deploy with:
//   az deployment sub create \
//     --location eastus \
//     --template-file infra/main.bicep \
//     --parameters infra/main.parameters.example.json
//
// Notes
// - Provider API keys default to empty strings so the stack deploys without
//   credentials. Set them after deploy (or pass them in via --parameters).
// - GHCR is the default registry. Set `containerImage` to point at your
//   pushed image; the placeholder works for the very first deploy because
//   the Container App tolerates an unreachable image when minReplicas=0.

targetScope = 'subscription'

@description('Azure region for all resources.')
param location string = 'eastus'

@description('Short prefix used to compose every resource name. Lowercase, 3-20 chars.')
@minLength(3)
@maxLength(20)
param namePrefix string = 'tradingagents'

@description('Optional suffix for unique deploys (e.g. dev, prod). Lowercase, ≤6 chars.')
@maxLength(6)
param environmentName string = 'prod'

@description('Container image for the FastAPI backend (GHCR or ACR).')
param containerImage string = 'ghcr.io/jinyiabc/tradingagents-server:latest'

@description('Container App minimum replicas. 0 = scale-to-zero.')
param minReplicas int = 0

@description('Container App maximum replicas. Keep at 1 for v1 (single-writer SQLite).')
param maxReplicas int = 1

@description('vCPU per replica (Container Apps consumption plan).')
param cpu string = '1.0'

@description('Memory per replica (Container Apps consumption plan).')
param memory string = '2.0Gi'

@description('Comma-separated origins for CORS, or "*" to allow all (dev only).')
param corsOrigins string = '*'

@secure()
@description('OpenAI API key. Leave empty if unused.')
param openaiApiKey string = ''

@secure()
@description('Anthropic API key. Leave empty if unused.')
param anthropicApiKey string = ''

@secure()
@description('Google (Gemini) API key. Leave empty if unused.')
param googleApiKey string = ''

@secure()
@description('xAI API key. Leave empty if unused.')
param xaiApiKey string = ''

@secure()
@description('DeepSeek API key. Leave empty if unused.')
param deepseekApiKey string = ''

@description('Enable Container Apps Easy Auth with Azure AD (M6). When true, AAD params must be set.')
param enableAuth bool = false

@description('AAD application (client) ID. Required when enableAuth=true.')
param aadClientId string = ''

@secure()
@description('AAD application client secret. Required when enableAuth=true.')
param aadClientSecret string = ''

@description('AAD tenant ID. Defaults to the deploying subscription tenant.')
param aadTenantId string = ''

@description('Allowed audiences (comma-separated). Empty = anyone in the tenant.')
param aadAllowedAudience string = ''

var rgName = '${namePrefix}-${environmentName}-rg'

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: rgName
  location: location
}

module resources 'resources.bicep' = {
  name: 'resources-${environmentName}'
  scope: rg
  params: {
    location: location
    namePrefix: namePrefix
    environmentName: environmentName
    containerImage: containerImage
    minReplicas: minReplicas
    maxReplicas: maxReplicas
    cpu: cpu
    memory: memory
    corsOrigins: corsOrigins
    openaiApiKey: openaiApiKey
    anthropicApiKey: anthropicApiKey
    googleApiKey: googleApiKey
    xaiApiKey: xaiApiKey
    deepseekApiKey: deepseekApiKey
    enableAuth: enableAuth
    aadClientId: aadClientId
    aadClientSecret: aadClientSecret
    aadTenantId: empty(aadTenantId) ? subscription().tenantId : aadTenantId
    aadAllowedAudience: aadAllowedAudience
  }
}

output resourceGroupName string = rg.name
output backendFqdn string = resources.outputs.backendFqdn
output staticWebAppHostname string = resources.outputs.staticWebAppHostname
output storageAccountName string = resources.outputs.storageAccountName
