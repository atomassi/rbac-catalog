// ============================================================================
// Module: Automation Account + housekeeping runbooks
// ============================================================================
// Creates the Automation Account and two PowerShell runbooks:
//   - ACR-Cleanup       : prunes ACR repositories (daily).
//   - PPE-Auto-Shutdown : stops the PPE deployment slot off-hours.
//
// Runbook PowerShell bodies are uploaded after Bicep creates the empty
// drafts here (see ../runbooks/). Schedules are out of scope — they carry
// start times that drift across deployments; create them once in the portal.
// ============================================================================

metadata description = 'Automation Account with two least-privilege housekeeping runbooks (ACR cleanup + PPE slot shutdown).'

@description('Automation account name.')
param accountName string

@description('Azure region.')
param location string

@description('Resource tags.')
param tags object

@description('Container registry name (passed to ACR-Cleanup via an automation variable).')
param acrName string

@description('App Service name (passed to PPE-Auto-Shutdown via an automation variable).')
param appServiceName string

@description('Create the PPE-Auto-Shutdown runbook (only useful with deployment slots).')
param runPpeShutdown bool = false

@description('How many image tags to keep per repository (ACR-Cleanup parameter).')
param acrKeepLastN int = 10

@description('Log Analytics workspace ID. When non-empty, runbook job logs and metrics are forwarded for monitoring.')
param logAnalyticsId string = ''

resource account 'Microsoft.Automation/automationAccounts@2023-11-01' = {
  name: accountName
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    sku: { name: 'Free' }
    publicNetworkAccess: true
  }
}

// Variables consumed by the runbooks
resource varAcr 'Microsoft.Automation/automationAccounts/variables@2023-11-01' = {
  parent: account
  name: 'AcrName'
  properties: { value: '"${acrName}"', isEncrypted: false }
}
resource varKeep 'Microsoft.Automation/automationAccounts/variables@2023-11-01' = {
  parent: account
  name: 'AcrKeepLastN'
  properties: { value: string(acrKeepLastN), isEncrypted: false }
}
resource varRg 'Microsoft.Automation/automationAccounts/variables@2023-11-01' = {
  parent: account
  name: 'ResourceGroupName'
  properties: { value: '"${resourceGroup().name}"', isEncrypted: false }
}
resource varApp 'Microsoft.Automation/automationAccounts/variables@2023-11-01' = if (runPpeShutdown) {
  parent: account
  name: 'AppServiceName'
  properties: { value: '"${appServiceName}"', isEncrypted: false }
}

resource acrCleanup 'Microsoft.Automation/automationAccounts/runbooks@2023-11-01' = {
  parent: account
  name: 'ACR-Cleanup'
  location: location
  tags: tags
  properties: {
    runbookType: 'PowerShell72'
    logProgress: false
    logVerbose: false
    description: 'Prunes ACR repositories: deletes untagged manifests and keeps the latest N tags per repo.'
  }
}

resource ppeShutdown 'Microsoft.Automation/automationAccounts/runbooks@2023-11-01' = if (runPpeShutdown) {
  parent: account
  name: 'PPE-Auto-Shutdown'
  location: location
  tags: tags
  properties: {
    runbookType: 'PowerShell72'
    logProgress: false
    logVerbose: false
    description: 'Stops the PPE deployment slot off-hours to save cost.'
  }
}

// Existing references (so role assignments can be scoped to the resource, not the RG).
resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' existing = {
  name: acrName
}
resource webApp 'Microsoft.Web/sites@2024-04-01' existing = {
  name: appServiceName
}

// Least-privilege role grants on the Automation Account managed identity:
//   - AcrDelete on the ACR             — needed to prune manifests in ACR-Cleanup.
//   - Website Contributor on the App   — needed to start/stop the PPE slot.
// Both are scoped to the individual resource, NOT the resource group.
var acrDeleteRoleId         = 'c2f4ef07-c644-48eb-af81-4b1b4947fb11'
var websiteContributorRoleId = 'de139f84-1756-47ae-9be6-808fbbe84772'

resource raAcrDelete 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, account.id, acrDeleteRoleId)
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrDeleteRoleId)
    principalId: account.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource raWebsiteContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (runPpeShutdown) {
  name: guid(webApp.id, account.id, websiteContributorRoleId)
  scope: webApp
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', websiteContributorRoleId)
    principalId: account.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Job logs + metrics to Log Analytics.
resource diagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = if (!empty(logAnalyticsId)) {
  name: 'AutomationLogs'
  scope: account
  properties: {
    workspaceId: logAnalyticsId
    logs: [
      { categoryGroup: 'allLogs', enabled: true }
    ]
    metrics: [ { category: 'AllMetrics', enabled: true } ]
  }
}

output accountId string = account.id
