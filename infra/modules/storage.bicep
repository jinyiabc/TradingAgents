@description('Storage account name (3-24 lowercase alphanumeric chars).')
@minLength(3)
@maxLength(24)
param name string

@description('Azure region.')
param location string

@description('File share name for ~/.tradingagents/ state.')
param fileShareName string

@description('File share quota in GiB.')
@minValue(1)
@maxValue(102400)
param fileShareQuotaGiB int = 10

resource storageAccount 'Microsoft.Storage/storageAccounts@2024-01-01' = {
  name: name
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: true
    supportsHttpsTrafficOnly: true
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

resource fileServices 'Microsoft.Storage/storageAccounts/fileServices@2024-01-01' = {
  parent: storageAccount
  name: 'default'
  properties: {
    shareDeleteRetentionPolicy: {
      enabled: true
      days: 7
    }
  }
}

resource fileShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2024-01-01' = {
  parent: fileServices
  name: fileShareName
  properties: {
    shareQuota: fileShareQuotaGiB
    enabledProtocols: 'SMB'
    accessTier: 'Hot'
  }
}

output accountName string = storageAccount.name
output accountId string = storageAccount.id
output fileShareName string = fileShare.name
#disable-next-line outputs-should-not-contain-secrets
output primaryKey string = storageAccount.listKeys().keys[0].value
