// Container Apps built-in authentication ("Easy Auth") for Azure AD.
// See docs/azure-web-deployment.md M6 for the one-time AAD app registration.

@description('Name of the existing Container App.')
param containerAppName string

@description('Azure AD application (client) ID for this app.')
param aadClientId string

@description('Azure AD tenant ID (issuer suffix).')
param aadTenantId string

@description('Comma-separated list of allowed user object IDs or emails. Empty = anyone in the tenant.')
param allowedAudience string = ''

resource app 'Microsoft.App/containerApps@2024-03-01' existing = {
  name: containerAppName
}

// Container App secret for the AAD client secret — referenced from the
// authConfig below. The app's own secret list lives on the Container App
// resource itself; we set the secret value via az or template upgrade.
resource authConfig 'Microsoft.App/containerApps/authConfigs@2024-03-01' = {
  parent: app
  name: 'current'
  properties: {
    platform: {
      enabled: true
    }
    globalValidation: {
      // Return 401 for API callers (so the frontend can detect and redirect),
      // while still serving /.auth/login/aad for browser-initiated sign-in.
      unauthenticatedClientAction: 'Return401'
      excludedPaths: [
        '/healthz'
      ]
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: aadClientId
          clientSecretSettingName: 'aad-client-secret'
          openIdIssuer: '${environment().authentication.loginEndpoint}${aadTenantId}/v2.0'
        }
        validation: {
          allowedAudiences: empty(allowedAudience) ? [] : split(allowedAudience, ',')
        }
      }
    }
    login: {
      tokenStore: {
        enabled: true
      }
      // Allow the frontend (different origin) to be sent back to after login.
      // The post-login redirect URL is passed by the caller as ?post_login_redirect_uri=.
      preserveUrlFragmentsForLogins: true
    }
  }
}

output enabled bool = authConfig.properties.platform.enabled
