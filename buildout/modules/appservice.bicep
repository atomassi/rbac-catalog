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
// Scan / scheduler settings — PRODUCTION SLOT ONLY
// ---------------------------------------------------------------------------
// The role + operations scans write the canonical catalog rows. Running
// them in parallel from staging or ppe would race the production worker
// and double-count events in role_history. Enabling them only on the
// production slot keeps a single writer.
//
// Both ROLE_SCAN_ENABLED and OPERATIONS_SCAN_ENABLED, the on-startup
// flags, and the poll intervals are listed in ``slotConfigNames`` below so
// a slot swap keeps the scanner ON the production slot (and OFF the
// pre-swap staging slot).
var scanProdSettings = [
  { name: 'ROLE_SCAN_ENABLED',              value: 'true' }
  { name: 'OPERATIONS_SCAN_ENABLED',        value: 'true' }
  // Run both scans on first startup so a brand-new deployment populates
  // the role + operation catalog immediately, instead of waiting for the
  // 2h / 24h scheduler tick.
  { name: 'RUN_SCAN_ON_STARTUP',            value: 'true' }
  { name: 'RUN_OPERATIONS_SCAN_ON_STARTUP', value: 'true' }
  { name: 'ROLES_POLL_INTERVAL_SECONDS',    value: '7200' }
  { name: 'OPERATIONS_POLL_INTERVAL_SECONDS', value: '86400' }
]
var scanDisabledSettings = [
  { name: 'ROLE_SCAN_ENABLED',              value: 'false' }
  { name: 'OPERATIONS_SCAN_ENABLED',        value: 'false' }
  { name: 'RUN_SCAN_ON_STARTUP',            value: 'false' }
  { name: 'RUN_OPERATIONS_SCAN_ON_STARTUP', value: 'false' }
  { name: 'ROLES_POLL_INTERVAL_SECONDS',    value: '7200' }
  { name: 'OPERATIONS_POLL_INTERVAL_SECONDS', value: '86400' }
]

// Resolve the production environment label.
//   * environmentName == 'prod' is the buildout naming token; the runtime
//     value is 'production' so telemetry matches the public site.
var prodEnvLabel = environmentName == 'prod' ? 'production' : environmentName

// APP_ENVIRONMENT_NAME, MSI_DB_USER, and the scan flags are per-slot:
//   * APP_ENVIRONMENT_NAME → telemetry / operator log distinction.
//   * MSI_DB_USER          → each slot has its OWN system-assigned identity
//                            and therefore its own pgaadauth-registered PG
//                            role. ``scripts/grant-postgres-aad-admin.sh``
//                            creates roles named ``<app>``, ``<app>-slot-staging``,
//                            ``<app>-slot-ppe``.
//   * ROLE_SCAN_ENABLED /  → only production runs the scans (single
//     OPERATIONS_SCAN_ENABLED  writer; staging/ppe would race the DB).
//
// All these names are listed in ``slotConfigNames`` below so a slot swap
// does NOT carry the labels or the scanner with the code — otherwise a
// staging→production swap would make the (now-production) slot use the
// staging PG role and pollute staging telemetry.
var prodAppSettings    = concat(commonAppSettings, scanProdSettings, [
  { name: 'APP_ENVIRONMENT_NAME', value: prodEnvLabel }
  { name: 'MSI_DB_USER',          value: postgresUser }
])
var stagingAppSettings = concat(commonAppSettings, scanDisabledSettings, [
  { name: 'APP_ENVIRONMENT_NAME', value: 'staging' }
  { name: 'MSI_DB_USER',          value: '${postgresUser}-slot-staging' }
])
var ppeAppSettings     = concat(commonAppSettings, scanDisabledSettings, [
  { name: 'APP_ENVIRONMENT_NAME', value: 'ppe' }
  { name: 'MSI_DB_USER',          value: '${postgresUser}-slot-ppe' }
])

var commonSiteConfigBase = {
  linuxFxVersion: 'DOCKER|${acrLoginServer}/azurerbac:${imageTag}'
  acrUseManagedIdentityCreds: true
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

resource app 'Microsoft.Web/sites@2024-04-01' = {
  name: appName
  location: location
  tags: tags
  kind: 'app,linux,container'
  identity: { type: 'SystemAssigned' }
  properties: prodSiteProperties
}

resource stagingSlot 'Microsoft.Web/sites/slots@2024-04-01' = if (deploySlots) {
  parent: app
  name: 'staging'
  location: location
  tags: tags
  kind: 'app,linux,container'
  identity: { type: 'SystemAssigned' }
  properties: stagingSiteProperties
}

resource ppeSlot 'Microsoft.Web/sites/slots@2024-04-01' = if (deploySlots) {
  parent: app
  name: 'ppe'
  location: location
  tags: tags
  kind: 'app,linux,container'
  identity: { type: 'SystemAssigned' }
  properties: ppeSiteProperties
}

// Pin per-slot settings so a swap does NOT move them with the code.
// Otherwise a staging→production swap would make the (now-production)
// slot keep ``APP_ENVIRONMENT_NAME=staging``, ``MSI_DB_USER=<app>-slot-staging``
// and (worst of all) keep the scanner OFF in the production slot while
// running it twice on the new staging slot.
resource appSlotConfigNames 'Microsoft.Web/sites/config@2024-04-01' = if (deploySlots) {
  parent: app
  name: 'slotConfigNames'
  properties: {
    appSettingNames: [
      'APP_ENVIRONMENT_NAME'
      'MSI_DB_USER'
      'ROLE_SCAN_ENABLED'
      'OPERATIONS_SCAN_ENABLED'
      'RUN_SCAN_ON_STARTUP'
      'RUN_OPERATIONS_SCAN_ON_STARTUP'
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
output appServicePrincipalId string = app.identity.principalId

// Slot-specific managed-identity principal IDs. Empty strings when
// ``deploySlots = false`` so callers can pass them through bicep
// without conditional wiring; the postgres-AAD-admin script skips
// empties. Slot identities need their own ACR pull + PostgreSQL
// grants — production-identity grants do NOT cover them.
// ``stagingSlot``/``ppeSlot`` only exist when ``deploySlots`` is true;
// the ``!.`` operator tells Bicep this access path is guarded.
output stagingSlotPrincipalId string = deploySlots ? stagingSlot!.identity.principalId : ''
output ppeSlotPrincipalId     string = deploySlots ? ppeSlot!.identity.principalId     : ''
