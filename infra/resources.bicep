// Resource-group-scope orchestrator. Wires the modules together.

@description('Azure region.')
param location string

@description('Short name prefix.')
param namePrefix string

@description('Environment suffix (e.g. dev, prod).')
param environmentName string

@description('Container image for the FastAPI backend.')
param containerImage string

@description('Min replicas for the backend.')
param minReplicas int

@description('Max replicas for the backend.')
param maxReplicas int

@description('vCPU per replica.')
param cpu string

@description('Memory per replica.')
param memory string

@description('CORS origins for the backend.')
param corsOrigins string

@secure()
param openaiApiKey string

@secure()
param anthropicApiKey string

@secure()
param googleApiKey string

@secure()
param xaiApiKey string

@secure()
param deepseekApiKey string

var resourceToken = uniqueString(resourceGroup().id, namePrefix, environmentName)
var fileShareName = 'tradingagents-data'

module logs 'modules/log-analytics.bicep' = {
  name: 'logs'
  params: {
    name: '${namePrefix}-${environmentName}-logs'
    location: location
  }
}

module storage 'modules/storage.bicep' = {
  name: 'storage'
  params: {
    // Storage account names must be 3-24 chars, lowercase alphanumeric. The
    // resourceToken from uniqueString() is exactly 13 chars (a-z0-9), so
    // 'ta' + token = 15 chars — well within the 24-char limit.
    name: 'ta${resourceToken}'
    location: location
    fileShareName: fileShareName
  }
}

module containerEnv 'modules/container-app-env.bicep' = {
  name: 'container-env'
  params: {
    name: '${namePrefix}-${environmentName}-env'
    location: location
    logAnalyticsWorkspaceCustomerId: logs.outputs.customerId
    logAnalyticsWorkspaceSharedKey: logs.outputs.sharedKey
    storageAccountName: storage.outputs.accountName
    storageAccountKey: storage.outputs.primaryKey
    fileShareName: fileShareName
    fileShareStorageName: 'data'
  }
}

module backend 'modules/container-app.bicep' = {
  name: 'backend'
  params: {
    name: '${namePrefix}-${environmentName}-api'
    location: location
    environmentId: containerEnv.outputs.id
    image: containerImage
    minReplicas: minReplicas
    maxReplicas: maxReplicas
    cpu: cpu
    memory: memory
    corsOrigins: corsOrigins
    fileShareStorageName: 'data'
    openaiApiKey: openaiApiKey
    anthropicApiKey: anthropicApiKey
    googleApiKey: googleApiKey
    xaiApiKey: xaiApiKey
    deepseekApiKey: deepseekApiKey
  }
}

module frontend 'modules/static-web-app.bicep' = {
  name: 'frontend'
  params: {
    name: '${namePrefix}-${environmentName}-web'
    // Static Web Apps GA SKU is regional: westeurope, eastus2, eastasia,
    // westus2, centralus. Fall back to eastus2 when the chosen location
    // isn't supported.
    location: 'eastus2'
    backendFqdn: backend.outputs.fqdn
  }
}

output backendFqdn string = backend.outputs.fqdn
output staticWebAppHostname string = frontend.outputs.hostname
output storageAccountName string = storage.outputs.accountName
