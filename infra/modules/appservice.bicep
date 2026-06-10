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

@description('Resource ID of the writer user-assigned identity (attached to the production slot).')
param writerResourceId string

@description('Client ID of the writer user-assigned identity (DB token + ACR pull on the production slot).')
param writerClientId string

@description('PostgreSQL role the production slot connects as (matches the writer identity name).')
param writerDbRole string

@description('Resource ID of the reader user-assigned identity (attached to the staging/ppe slots). Empty when slots are not deployed.')
param readerResourceId string = ''

@description('Client ID of the reader user-assigned identity (DB token + ACR pull on the staging/ppe slots).')
param readerClientId string = ''

@description('PostgreSQL role the staging/ppe slots connect as (matches the reader identity name).')
param readerDbRole string = ''

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
  // Point the App Service warmup probe at ``/healthz`` instead of the
  // default ``/``. The homepage triggers full cache initialization and
  // AI-model warmup (~3-4 min on cold start), which exceeds the
  // platform's HTTP-ping timeout and causes spurious "container did not
  // start" errors in the platform log. ``/healthz`` returns a cheap
  // 200 once the lifespan has completed.
  { name: 'WEBSITE_WARMUP_PATH', value: '/healthz' }
  // Tell App Service to use the system MI when pulling from ACR. Without
  // ``DOCKER_REGISTRY_SERVER_URL`` the platform falls back to ACR admin
  // credentials, which fails with "admin credentials on ACR are disabled"
  // because ``adminUserEnabled = false`` in acr.bicep.
  { name: 'DOCKER_REGISTRY_SERVER_URL', value: 'https://${acrLoginServer}' }
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
  { name: 'USE_MANAGED_IDENTITY', value: 'true' }
  { name: 'MSI_DB_HOST', value: postgresHost }
  { name: 'MSI_DB_PORT', value: '5432' }
  { name: 'MSI_DB_NAME', value: postgresDatabase }
  { name: 'OLLAMA_BASE_URL', value: ollamaBaseUrl }
  { name: 'OLLAMA_MODEL', value: 'qwen-rbac-v5' }
  { name: 'LOG_LEVEL', value: 'INFO' }
  { name: 'MCP_SERVER_ENABLED', value: 'true' }
]

// ---------------------------------------------------------------------------
// Scan / scheduler settings
// ---------------------------------------------------------------------------
// The role + operations scans write the canonical catalog rows, and only ONE
// writer may persist them — the production slot. Running real (committing)
// scans from staging or ppe would race the production worker and double-count
// events in role_history.
//
// Non-production slots therefore run the same scans in WHAT-IF mode
// (``SCAN_DRY_RUN=true``): they fetch from Azure and log every change they
// would make, but never write to the database. This surfaces drift on each
// slot without a second writer (and dovetails with the SELECT-only reader DB
// role those slots authenticate as).
//
// SCAN_DRY_RUN, ROLE_SCAN_ENABLED, and OPERATIONS_SCAN_ENABLED are listed in
// ``slotConfigNames`` below so a slot swap keeps the real (committing) scan ON
// the production slot and what-if mode ON the pre-swap staging slot. The poll
// intervals are identical across slots, so they are NOT slot-sticky (a swap
// can't change them) and are intentionally omitted from ``slotConfigNames``.
var scanProdSettings = [
  { name: 'ROLE_SCAN_ENABLED',              value: 'true' }
  { name: 'OPERATIONS_SCAN_ENABLED',        value: 'true' }
  { name: 'SCAN_DRY_RUN',                   value: 'false' }
  { name: 'ROLES_POLL_INTERVAL_SECONDS',    value: '7200' }
  { name: 'OPERATIONS_POLL_INTERVAL_SECONDS', value: '86400' }
]
// Non-production slots: scans run, but in what-if mode (no DB writes).
var scanDryRunSettings = [
  { name: 'ROLE_SCAN_ENABLED',              value: 'true' }
  { name: 'OPERATIONS_SCAN_ENABLED',        value: 'true' }
  { name: 'SCAN_DRY_RUN',                   value: 'true' }
  { name: 'ROLES_POLL_INTERVAL_SECONDS',    value: '7200' }
  { name: 'OPERATIONS_POLL_INTERVAL_SECONDS', value: '86400' }
]

// Resolve the production environment label.
//   * environmentName == 'prod' is the infra naming token; the runtime
//     value is 'production' so telemetry matches the public site.
var prodEnvLabel = environmentName == 'prod' ? 'production' : environmentName

// APP_ENVIRONMENT_NAME, MSI_DB_USER, MSI_CLIENT_ID, and the scan flags are
// per-slot:
//   * APP_ENVIRONMENT_NAME → telemetry / operator log distinction.
//   * MSI_CLIENT_ID        → which user-assigned identity the app gets its
//                            PostgreSQL token for (writer on production,
//                            reader on staging/ppe).
//   * MSI_DB_USER          → the pgaadauth role to connect as. Production
//                            uses the writer role (full CRUD); staging/ppe
//                            use the reader role (SELECT only), so a
//                            non-production slot physically cannot write.
//   * SCAN_DRY_RUN /       → production commits scan results; staging/ppe
//     ROLE_SCAN_ENABLED /     run the same scans in what-if mode (no writes)
//     OPERATIONS_SCAN_ENABLED so only the single production writer persists.
//
// All these names are listed in ``slotConfigNames`` below so a slot swap
// does NOT carry the labels or the scanner mode with the code — otherwise a
// staging→production swap would make the (now-production) slot use the
// reader role/identity and stay in what-if mode.
var prodAppSettings    = concat(commonAppSettings, scanProdSettings, [
  { name: 'APP_ENVIRONMENT_NAME', value: prodEnvLabel }
  { name: 'MSI_CLIENT_ID',        value: writerClientId }
  { name: 'MSI_DB_USER',          value: writerDbRole }
])
var stagingAppSettings = concat(commonAppSettings, scanDryRunSettings, [
  { name: 'APP_ENVIRONMENT_NAME', value: 'staging' }
  { name: 'MSI_CLIENT_ID',        value: readerClientId }
  { name: 'MSI_DB_USER',          value: readerDbRole }
])
var ppeAppSettings     = concat(commonAppSettings, scanDryRunSettings, [
  { name: 'APP_ENVIRONMENT_NAME', value: 'ppe' }
  { name: 'MSI_CLIENT_ID',        value: readerClientId }
  { name: 'MSI_DB_USER',          value: readerDbRole }
])

var commonSiteConfigBase = {
  linuxFxVersion: 'DOCKER|${acrLoginServer}/rbaccatalog:${imageTag}'
  acrUseManagedIdentityCreds: true
  alwaysOn: sku != 'B1' && sku != 'F1'
  http20Enabled: true
  ftpsState: 'Disabled'
  minTlsVersion: minTlsVersion
  scmMinTlsVersion: minTlsVersion
}

// Properties shared between the production slot, staging, and ppe — but
// each gets its own ``appSettings`` (APP_ENVIRONMENT_NAME etc. differ per
// slot) and its own ``acrUserManagedIdentityID`` so the platform pulls the
// private image using that slot's attached user-assigned identity.
var prodSiteProperties = {
  serverFarmId: plan.id
  httpsOnly: httpsOnly
  siteConfig: union(commonSiteConfigBase, {
    appSettings: prodAppSettings
    acrUserManagedIdentityID: writerClientId
  })
}
var stagingSiteProperties = {
  serverFarmId: plan.id
  httpsOnly: httpsOnly
  siteConfig: union(commonSiteConfigBase, {
    appSettings: stagingAppSettings
    acrUserManagedIdentityID: readerClientId
  })
}
var ppeSiteProperties = {
  serverFarmId: plan.id
  httpsOnly: httpsOnly
  siteConfig: union(commonSiteConfigBase, {
    appSettings: ppeAppSettings
    acrUserManagedIdentityID: readerClientId
  })
}

resource app 'Microsoft.Web/sites@2024-04-01' = {
  name: appName
  location: location
  tags: tags
  kind: 'app,linux,container'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${writerResourceId}': {} }
  }
  properties: prodSiteProperties
}

resource stagingSlot 'Microsoft.Web/sites/slots@2024-04-01' = if (deploySlots) {
  parent: app
  name: 'staging'
  location: location
  tags: tags
  kind: 'app,linux,container'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${readerResourceId}': {} }
  }
  properties: stagingSiteProperties
}

resource ppeSlot 'Microsoft.Web/sites/slots@2024-04-01' = if (deploySlots) {
  parent: app
  name: 'ppe'
  location: location
  tags: tags
  kind: 'app,linux,container'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${readerResourceId}': {} }
  }
  properties: ppeSiteProperties
}

// Pin per-slot settings so a swap does NOT move them with the code.
// Otherwise a staging→production swap would make the (now-production)
// slot keep ``APP_ENVIRONMENT_NAME=staging``, the reader identity/role
// and (worst of all) stay in what-if mode (``SCAN_DRY_RUN=true``) so the
// production slot never persists scans while the pre-swap staging slot
// becomes the only committing writer.
resource appSlotConfigNames 'Microsoft.Web/sites/config@2024-04-01' = if (deploySlots) {
  parent: app
  name: 'slotConfigNames'
  properties: {
    appSettingNames: [
      'APP_ENVIRONMENT_NAME'
      'MSI_CLIENT_ID'
      'MSI_DB_USER'
      'SCAN_DRY_RUN'
      'ROLE_SCAN_ENABLED'
      'OPERATIONS_SCAN_ENABLED'
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
