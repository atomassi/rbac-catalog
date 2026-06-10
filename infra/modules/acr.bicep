// ============================================================================
// Module: Azure Container Registry
// ============================================================================
// Creates the ACR and grants AcrPull to the web + scan user-assigned managed
// identities (both pull the same private image).
// ============================================================================

metadata description = 'Basic-tier Azure Container Registry with AcrPull granted to the web + scan user-assigned managed identities.'

@description('ACR name. Globally unique, alphanumeric only (5-50 chars).')
param name string

@description('Azure region.')
param location string

@description('Resource tags.')
param tags object

@description('Principal ID of the web user-assigned managed identity (granted AcrPull so the web tier can pull the image).')
param webIdentityPrincipalId string

@description('Principal ID of the scan user-assigned managed identity (granted AcrPull so the scan Jobs can pull the image).')
param scanIdentityPrincipalId string

@description('Log Analytics workspace ID. When non-empty, registry events are forwarded for security monitoring.')
param logAnalyticsId string = ''

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: name
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false
    anonymousPullEnabled: false
    publicNetworkAccess: 'Enabled'
    // NOTE: ACR's built-in ``policies.retentionPolicy`` is a Premium-SKU
    // feature and fails deployment on Basic registries. The infra does
    // not provision Automation runbooks, so prune stale manifests out of
    // band when needed (or switch to Premium and re-enable the policy
    // here if/when image volume justifies it).
  }
}

// AcrPull built-in role definition ID
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

// Both identities pull the same image, so each gets an AcrPull assignment.
resource acrPullWeb 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, webIdentityPrincipalId, acrPullRoleId)
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: webIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource acrPullScan 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, scanIdentityPrincipalId, acrPullRoleId)
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: scanIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Login + push/pull events to Log Analytics (security monitoring + forensics).
resource diagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = if (!empty(logAnalyticsId)) {
  name: 'AcrLogs'
  scope: acr
  properties: {
    workspaceId: logAnalyticsId
    logs: [
      { categoryGroup: 'allLogs', enabled: true }
    ]
    metrics: [ { category: 'AllMetrics', enabled: true } ]
  }
}

output id string = acr.id
output name string = acr.name
output loginServer string = acr.properties.loginServer
