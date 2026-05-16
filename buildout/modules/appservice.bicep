// ============================================================================
// Module: App Service Plan + App Service (Docker, system MI) + optional slots
// ============================================================================
// Production slot, staging, and ppe all share `siteProperties` so the three
// resources stay in sync. Diagnostic logs forward to Log Analytics.
// ============================================================================

metadata description = 'Linux App Service Plan + Docker-based App Service with system-assigned managed identity and optional staging/ppe slots.'

@description('App Service name. Globally unique.')
param appName string

@description('App Service Plan name.')
param planName string

@description('Azure region.')
param location string

@description('Resource tags.')
param tags object

@description('App Service Plan SKU (B1, P0v3, P1v3, ...).')
param sku string

@description('ACR login server (e.g. myappregistry.azurecr.io).')
param acrLoginServer string

@description('Application Insights connection string.')
param appInsightsConnectionString string

@description('Log Analytics workspace resource ID (for diagnostic settings).')
param logAnalyticsId string

@description('PostgreSQL fully-qualified domain name.')
param postgresHost string

@description('PostgreSQL database name.')
param postgresDatabase string = 'azurerbac'

@description('PG role used by the AAD-token connection (matches the App Service name — its MI display name).')
param postgresUser string

@description('Resource ID of the App Service integration subnet. Empty = no VNet integration.')
param appServiceSubnetId string

@description('Environment name. Drives APP_ENVIRONMENT_NAME.')
param environmentName string

@description('Provision staging + ppe deployment slots.')
param deploySlots bool

@description('Ollama base URL. Empty = AI features disabled.')
param ollamaBaseUrl string

@description('Container image tag the App Service tries to pull on first start.')
param imageTag string = 'latest'

@description('Enforce HTTPS-only inbound traffic on the App Service. Default: true. Set to false ONLY if you are fronting the app with a reverse proxy in HTTP-only/Flexible mode (e.g. Cloudflare Flexible).')
param httpsOnly bool = true

@description('Minimum TLS version accepted on the inbound connection.')
@allowed([ '1.2', '1.3' ])
param minTlsVersion string = '1.2'

resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: planName
  location: location
  tags: tags
  kind: 'linux'
  sku: { name: sku }
  properties: { reserved: true }
}

var commonAppSettings = [
  { name: 'WEBSITES_PORT', value: '8000' }
  { name: 'WEBSITES_ENABLE_APP_SERVICE_STORAGE', value: 'false' }
  { name: 'APP_ENVIRONMENT_NAME', value: environmentName == 'prod' ? 'production' : environmentName }
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
  { name: 'USE_MANAGED_IDENTITY', value: 'true' }
  { name: 'MSI_DB_HOST', value: postgresHost }
  { name: 'MSI_DB_PORT', value: '5432' }
  { name: 'MSI_DB_NAME', value: postgresDatabase }
  { name: 'MSI_DB_USER', value: postgresUser }
  { name: 'OLLAMA_BASE_URL', value: ollamaBaseUrl }
  { name: 'OLLAMA_MODEL', value: 'qwen-rbac-v5' }
  { name: 'ROLE_SCAN_ENABLED', value: 'true' }
  { name: 'OPERATIONS_SCAN_ENABLED', value: 'true' }
  { name: 'RUN_SCAN_ON_STARTUP', value: 'false' }
  { name: 'ROLES_POLL_INTERVAL_SECONDS', value: '7200' }
  { name: 'OPERATIONS_POLL_INTERVAL_SECONDS', value: '86400' }
  { name: 'LOG_LEVEL', value: 'INFO' }
  { name: 'MCP_SERVER_ENABLED', value: 'true' }
]

var commonSiteConfig = {
  linuxFxVersion: 'DOCKER|${acrLoginServer}/azurerbac:${imageTag}'
  acrUseManagedIdentityCreds: true
  alwaysOn: sku != 'B1' && sku != 'F1'
  http20Enabled: true
  ftpsState: 'Disabled'
  minTlsVersion: minTlsVersion
  scmMinTlsVersion: minTlsVersion
  appSettings: commonAppSettings
}

// Properties shared between the production slot, staging, and ppe.
var siteProperties = {
  serverFarmId: plan.id
  virtualNetworkSubnetId: empty(appServiceSubnetId) ? null : appServiceSubnetId
  httpsOnly: httpsOnly
  siteConfig: commonSiteConfig
}

resource app 'Microsoft.Web/sites@2024-04-01' = {
  name: appName
  location: location
  tags: tags
  kind: 'app,linux,container'
  identity: { type: 'SystemAssigned' }
  properties: siteProperties
}

resource stagingSlot 'Microsoft.Web/sites/slots@2024-04-01' = if (deploySlots) {
  parent: app
  name: 'staging'
  location: location
  tags: tags
  kind: 'app,linux,container'
  identity: { type: 'SystemAssigned' }
  properties: siteProperties
}

resource ppeSlot 'Microsoft.Web/sites/slots@2024-04-01' = if (deploySlots) {
  parent: app
  name: 'ppe'
  location: location
  tags: tags
  kind: 'app,linux,container'
  identity: { type: 'SystemAssigned' }
  properties: siteProperties
}

// Forward all diagnostic logs to Log Analytics.
resource diagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'AppServiceLogs'
  scope: app
  properties: {
    workspaceId: logAnalyticsId
    logs: [
      { category: 'AppServiceHTTPLogs',     enabled: true }
      { category: 'AppServiceConsoleLogs',  enabled: true }
      { category: 'AppServiceAppLogs',      enabled: true }
      { category: 'AppServicePlatformLogs', enabled: true }
    ]
    metrics: [ { category: 'AllMetrics', enabled: true } ]
  }
}

output appServiceId string = app.id
output appServiceName string = app.name
output defaultHostname string = app.properties.defaultHostName
output appServicePrincipalId string = app.identity.principalId
