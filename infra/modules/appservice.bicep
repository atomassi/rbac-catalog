// ============================================================================
// Module: App Service Plan + App Service (Docker, user-assigned MI) + slots
// ============================================================================
// Production slot, staging, and ppe all share `siteProperties` so the three
// resources stay in sync, and all use the SAME web user-assigned identity (a
// SELECT-only PostgreSQL role — the web tier never writes; the scan Jobs own
// the schema). Diagnostic logs forward to Log Analytics.
// ============================================================================

metadata description = 'Linux App Service Plan + Docker-based App Service on the read-only web user-assigned managed identity with optional staging/ppe slots.'

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

@description('PG role used by the AAD-token connection (equals the web identity name; SELECT-only).')
param postgresUser string

@description('Resource ID of the web user-assigned managed identity.')
param userAssignedIdentityId string

@description('Client ID of the web user-assigned managed identity (AZURE_CLIENT_ID + ACR pull).')
param userAssignedIdentityClientId string

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
  // Point the App Service warmup probe at ``/healthz`` instead of the
  // default ``/``. The homepage triggers full cache initialization and
  // AI-model warmup (~3-4 min on cold start), which exceeds the
  // platform's HTTP-ping timeout and causes spurious "container did not
  // start" errors in the platform log. ``/healthz`` returns a cheap
  // 200 once the lifespan has completed.
  { name: 'WEBSITE_WARMUP_PATH', value: '/healthz' }
  // The web container builds the full in-memory role/operation cache on
  // boot (hundreds of seconds on a cold start), so raise the platform's
  // container-start timeout from its 230s default to 1800s to avoid the
  // site being marked unhealthy before the lifespan completes.
  { name: 'WEBSITES_CONTAINER_START_TIME_LIMIT', value: '1800' }
  // Select the shared user-assigned identity for both ACR pull (via
  // ``acrUserManagedIdentityID`` below) and the PostgreSQL AAD token
  // (``AZURE_CLIENT_ID`` is read by ManagedIdentityCredential).
  { name: 'AZURE_CLIENT_ID', value: userAssignedIdentityClientId }
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
  { name: 'USE_MANAGED_IDENTITY', value: 'true' }
  { name: 'MSI_DB_HOST', value: postgresHost }
  { name: 'MSI_DB_PORT', value: '5432' }
  { name: 'MSI_DB_NAME', value: postgresDatabase }
  { name: 'MSI_DB_USER', value: postgresUser }
  { name: 'OLLAMA_BASE_URL', value: ollamaBaseUrl }
  { name: 'OLLAMA_MODEL', value: 'qwen-rbac-v5' }
  { name: 'LOG_LEVEL', value: 'INFO' }
  { name: 'MCP_SERVER_ENABLED', value: 'true' }
]

// ---------------------------------------------------------------------------
// Per-slot app settings
// ---------------------------------------------------------------------------
// The role + operations scans no longer run in the App Service container. The
// image starts uvicorn only (see Dockerfile); the scans run as dedicated
// Container Apps Jobs (infra/modules/containerappjobs.bicep), which are the
// single writer for the catalog rows. The web tier never reads
// ROLE_SCAN_ENABLED / OPERATIONS_SCAN_ENABLED, so they are not set here.
//
// APP_ENVIRONMENT_NAME is per-slot (telemetry / operator log distinction).
// MSI_DB_USER is NOT per-slot anymore: every slot uses the same shared
// user-assigned identity, so they all connect as the same PostgreSQL role
// (set once in commonAppSettings). A slot swap therefore never moves the DB
// identity with the code.
var prodAppSettings    = concat(commonAppSettings, [
  // Always 'production' (a recognized runtime env), never the infra naming
  // token (e.g. 'dev'), which the app coerces to 'local' — disabling App
  // Insights and re-enabling file logging.
  { name: 'APP_ENVIRONMENT_NAME', value: 'production' }
])
var stagingAppSettings = concat(commonAppSettings, [
  { name: 'APP_ENVIRONMENT_NAME', value: 'staging' }
])
var ppeAppSettings     = concat(commonAppSettings, [
  { name: 'APP_ENVIRONMENT_NAME', value: 'ppe' }
])

var commonSiteConfigBase = {
  linuxFxVersion: 'DOCKER|${acrLoginServer}/rbaccatalog:${imageTag}'
  acrUseManagedIdentityCreds: true
  // Pull the private image with the shared user-assigned identity (its
  // client ID). Without this the platform would use the system-assigned MI,
  // which this app no longer has.
  acrUserManagedIdentityID: userAssignedIdentityClientId
  alwaysOn: sku != 'B1' && sku != 'F1'
  http20Enabled: true
  ftpsState: 'Disabled'
  minTlsVersion: minTlsVersion
  scmMinTlsVersion: minTlsVersion
}

// Properties shared between the production slot, staging, and ppe — but
// each gets its own ``appSettings`` so APP_ENVIRONMENT_NAME differs per
// slot.
var prodSiteProperties = {
  serverFarmId: plan.id
  httpsOnly: httpsOnly
  siteConfig: union(commonSiteConfigBase, { appSettings: prodAppSettings })
}
var stagingSiteProperties = {
  serverFarmId: plan.id
  httpsOnly: httpsOnly
  siteConfig: union(commonSiteConfigBase, { appSettings: stagingAppSettings })
}
var ppeSiteProperties = {
  serverFarmId: plan.id
  httpsOnly: httpsOnly
  siteConfig: union(commonSiteConfigBase, { appSettings: ppeAppSettings })
}

var sharedIdentity = {
  type: 'UserAssigned'
  userAssignedIdentities: {
    '${userAssignedIdentityId}': {}
  }
}

resource app 'Microsoft.Web/sites@2024-04-01' = {
  name: appName
  location: location
  tags: tags
  kind: 'app,linux,container'
  identity: sharedIdentity
  properties: prodSiteProperties
}

resource stagingSlot 'Microsoft.Web/sites/slots@2024-04-01' = if (deploySlots) {
  parent: app
  name: 'staging'
  location: location
  tags: tags
  kind: 'app,linux,container'
  identity: sharedIdentity
  properties: stagingSiteProperties
}

resource ppeSlot 'Microsoft.Web/sites/slots@2024-04-01' = if (deploySlots) {
  parent: app
  name: 'ppe'
  location: location
  tags: tags
  kind: 'app,linux,container'
  identity: sharedIdentity
  properties: ppeSiteProperties
}

// Pin per-slot settings so a swap does NOT move them with the code.
// Only APP_ENVIRONMENT_NAME is slot-specific now (MSI_DB_USER is identical
// across slots since they share one identity). The scan flags are handled by
// Container Apps Jobs and stay OFF on every slot, so nothing else to pin.
resource appSlotConfigNames 'Microsoft.Web/sites/config@2024-04-01' = if (deploySlots) {
  parent: app
  name: 'slotConfigNames'
  properties: {
    appSettingNames: [
      'APP_ENVIRONMENT_NAME'
    ]
  }
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
