@description('Static Web App name.')
param name string

@description('Azure region. Static Web Apps GA SKU is regional — use eastus2, westus2, centralus, eastasia, or westeurope.')
param location string

@description('Backend FQDN to expose to the frontend via app settings.')
param backendFqdn string

resource staticSite 'Microsoft.Web/staticSites@2024-04-01' = {
  name: name
  location: location
  sku: {
    name: 'Free'
    tier: 'Free'
  }
  properties: {
    // No GitHub integration in this template — wire that up in the M5
    // GitHub Actions workflow that runs npm run build + uploads to this
    // resource using its deployment token.
    allowConfigFileUpdates: true
  }
}

resource appSettings 'Microsoft.Web/staticSites/config@2024-04-01' = {
  parent: staticSite
  name: 'appsettings'
  properties: {
    // Available to the build pipeline so the frontend can be configured at
    // build time. The Next.js client reads NEXT_PUBLIC_API_BASE from the
    // environment.
    NEXT_PUBLIC_API_BASE: 'https://${backendFqdn}'
  }
}

output name string = staticSite.name
output hostname string = staticSite.properties.defaultHostname
output id string = staticSite.id
