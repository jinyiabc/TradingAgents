@description('Managed Environment name.')
param name string

@description('Azure region.')
param location string

@description('Log Analytics customerId (workspace ID).')
param logAnalyticsWorkspaceCustomerId string

@secure()
@description('Log Analytics primary shared key.')
param logAnalyticsWorkspaceSharedKey string

@description('Storage account name backing the file share.')
param storageAccountName string

@secure()
@description('Storage account key.')
param storageAccountKey string

@description('Name of the existing Azure Files share to expose to apps.')
param fileShareName string

@description('Name the Container App will reference this storage by.')
param fileShareStorageName string

resource managedEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: name
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsWorkspaceCustomerId
        sharedKey: logAnalyticsWorkspaceSharedKey
      }
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
    zoneRedundant: false
  }
}

resource fileStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: managedEnvironment
  name: fileShareStorageName
  properties: {
    azureFile: {
      accountName: storageAccountName
      accountKey: storageAccountKey
      shareName: fileShareName
      accessMode: 'ReadWrite'
    }
  }
}

output id string = managedEnvironment.id
output name string = managedEnvironment.name
output defaultDomain string = managedEnvironment.properties.defaultDomain
