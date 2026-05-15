@description('Container App name.')
param name string

@description('Azure region.')
param location string

@description('Managed Environment resource ID.')
param environmentId string

@description('Container image (e.g. ghcr.io/owner/repo:tag).')
param image string

@description('Min replicas (0 enables scale-to-zero).')
param minReplicas int

@description('Max replicas. Keep at 1 in v1 — multiple replicas race on the SQLite jobs table.')
param maxReplicas int

@description('vCPU per replica.')
param cpu string

@description('Memory per replica (e.g. "2.0Gi").')
param memory string

@description('CORS origins, "*" or comma-separated list.')
param corsOrigins string

@description('Managed-environment storage name to mount.')
param fileShareStorageName string

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

@secure()
@description('Azure AD client secret. Required only when authConfig is deployed (M6); empty otherwise.')
param aadClientSecret string = ''

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      secrets: [
        { name: 'openai-api-key', value: openaiApiKey }
        { name: 'anthropic-api-key', value: anthropicApiKey }
        { name: 'google-api-key', value: googleApiKey }
        { name: 'xai-api-key', value: xaiApiKey }
        { name: 'deepseek-api-key', value: deepseekApiKey }
        // Slot is always present so flipping enableAuth=true doesn't require
        // a separate Bicep deploy to add it. authConfigs/current references
        // `aad-client-secret` by name.
        { name: 'aad-client-secret', value: aadClientSecret }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: image
          resources: {
            cpu: json(cpu)
            memory: memory
          }
          env: [
            { name: 'HOST', value: '0.0.0.0' }
            { name: 'PORT', value: '8000' }
            { name: 'TRADINGAGENTS_CORS_ORIGINS', value: corsOrigins }
            { name: 'OPENAI_API_KEY', secretRef: 'openai-api-key' }
            { name: 'ANTHROPIC_API_KEY', secretRef: 'anthropic-api-key' }
            { name: 'GOOGLE_API_KEY', secretRef: 'google-api-key' }
            { name: 'XAI_API_KEY', secretRef: 'xai-api-key' }
            { name: 'DEEPSEEK_API_KEY', secretRef: 'deepseek-api-key' }
          ]
          volumeMounts: [
            {
              volumeName: 'data'
              mountPath: '/home/appuser/.tradingagents'
            }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/healthz'
                port: 8000
              }
              initialDelaySeconds: 30
              periodSeconds: 30
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/healthz'
                port: 8000
              }
              initialDelaySeconds: 5
              periodSeconds: 10
            }
          ]
        }
      ]
      volumes: [
        {
          name: 'data'
          storageType: 'AzureFile'
          storageName: fileShareStorageName
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
      }
    }
  }
}

output id string = containerApp.id
output name string = containerApp.name
output fqdn string = containerApp.properties.configuration.ingress.fqdn
