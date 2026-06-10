// ============================================================================
// Azure RBAC Catalog — single-file Bicep entry point
// ============================================================================
// Mandatory : App Service + ACR + PostgreSQL (AAD auth) + Log Analytics + App Insights
// Optional  : deployment slots (staging + ppe)
//
// Out of scope by design:
//   * Ollama VM / VNet — the public site runs without AI when
//     ``OLLAMA_BASE_URL`` is empty; bring your own Ollama endpoint and set
//     ``OLLAMA_BASE_URL`` post-deploy if you want AI features.
//   * Automation Account / housekeeping runbooks — not required to run
//     the site. ACR Basic does not support retention; prune images out of
//     band if/when needed.
//
// Edit parameters/*.bicepparam, then run `./scripts/deploy.sh`.
// ============================================================================

metadata name        = 'rbaccatalog-infra'
metadata description = 'Subscription-scoped deployment of the Azure RBAC Catalog (App Service + ACR + PostgreSQL + monitoring, with optional deployment slots).'

targetScope = 'subscription'

// ---------------------------------------------------------------------------
// Parameters
// ---------------------------------------------------------------------------

@description('Environment tag (drives naming suffixes and SKU defaults).')
@allowed([ 'prod', 'dev' ])
param environmentName string = 'prod'

@description('Resource group name to create or update.')
param resourceGroupName string = 'myapp-rg'

@description('Primary Azure region (App Service, monitoring).')
param location string = 'westeurope'

@description('Region for the App Service (plan + app). Defaults to `location`.')
param appServiceLocation string = location

@description('Region for the PostgreSQL Flexible Server. Defaults to `location`.')
param postgresLocation string = location

@description('Region for the Azure Container Registry. Defaults to `location`.')
param acrLocation string = location

@description('Base name (3-15 alphanumeric chars). Used as the prefix for every resource name.')
@minLength(3)
@maxLength(15)
param baseName string = 'myapp'

@description('App Service Plan SKU. P0v3 (default) / B3 are ideal; B2 is the practical minimum because the web server and the background scan worker share the plan, so B1\'s single core starves the uvicorn event loop during scans.')
param appServicePlanSku string = 'P0v3'

@description('PostgreSQL SKU name (e.g. Standard_B1ms).')
param postgresSkuName string = 'Standard_B1ms'

@description('PostgreSQL tier.')
@allowed([ 'Burstable', 'GeneralPurpose', 'MemoryOptimized' ])
param postgresTier string = 'Burstable'

@description('PostgreSQL storage size in GB.')
param postgresStorageGB int = 32

@description('PostgreSQL major version.')
param postgresVersion string = '17'

@description('PostgreSQL administrator username (initial bootstrap; the app uses Entra ID after).')
param postgresAdminUser string = 'pgadmin'

@description('PostgreSQL administrator password. 8-128 chars, mixed case + digit + symbol.')
@secure()
param postgresAdminPassword string

@description('Provision staging + ppe deployment slots. Recommended (not required) for blue/green deploys.')
param deploySlots bool = false

@description('Ollama endpoint reachable from the App Service. Empty = AI features disabled (the site still works for browsing and rule-based recommendations).')
param ollamaBaseUrl string = ''

@description('Enforce HTTPS-only on the App Service. Default: true. Set to false ONLY if fronting the app with a Cloudflare Flexible (HTTP-to-origin) reverse proxy.')
param appServiceHttpsOnly bool = true

@description('Allow all Azure-resident services to reach PostgreSQL via the special 0.0.0.0 firewall rule. Convenient but exposes the server to every Azure tenant. Set to false for production hardening.')
param postgresAllowAllAzureServices bool = true

@description('Enable password auth on PostgreSQL in addition to Entra ID. Required during first deployment so the AAD-mapped role can be provisioned. Set to false on subsequent deployments to remove the password attack surface.')
param postgresEnablePasswordAuth bool = true

// ---------------------------------------------------------------------------
// Derived names + tags
// ---------------------------------------------------------------------------

var n = {
  app:        '${baseName}-app'
  plan:       '${baseName}-plan'
  acr:        '${baseName}registry'
  pg:         '${baseName}-pg'
  logs:       '${baseName}-logs'
  insights:   '${baseName}-insights'
}

var tags = {
  application: baseName
  environment: environmentName
  managedBy: 'bicep'
}

// ---------------------------------------------------------------------------
// Resource group
// ---------------------------------------------------------------------------

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

// ---------------------------------------------------------------------------
// Modules
// ---------------------------------------------------------------------------

module monitoring 'modules/monitoring.bicep' = {
  scope: rg
  name: 'monitoring'
  params: { logAnalyticsName: n.logs, appInsightsName: n.insights, location: location, tags: tags }
}

module identity 'modules/identity.bicep' = {
  scope: rg
  name: 'identity'
  params: {
    baseName: baseName
    location: location
    tags: tags
    deploySlots: deploySlots
  }
}

module postgres 'modules/postgres.bicep' = {
  scope: rg
  name: 'postgres'
  params: {
    serverName: n.pg
    location: postgresLocation
    tags: tags
    administratorLogin: postgresAdminUser
    administratorPassword: postgresAdminPassword
    skuName: postgresSkuName
    tier: postgresTier
    storageSizeGB: postgresStorageGB
    version: postgresVersion
    enablePasswordAuth: postgresEnablePasswordAuth
    allowAllAzureServices: postgresAllowAllAzureServices
    logAnalyticsId: monitoring.outputs.logAnalyticsId
  }
}

module appService 'modules/appservice.bicep' = {
  scope: rg
  name: 'appservice'
  params: {
    appName: n.app
    planName: n.plan
    location: appServiceLocation
    tags: tags
    sku: appServicePlanSku
    acrLoginServer: '${n.acr}.azurecr.io'
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    logAnalyticsId: monitoring.outputs.logAnalyticsId
    postgresHost: postgres.outputs.fqdn
    // Writer identity (production slot) and reader identity (staging/ppe).
    // Each slot authenticates to PostgreSQL as its own pgaadauth role,
    // created by scripts/grant-postgres-aad-admin.sh to match the identity
    // name below.
    writerResourceId: identity.outputs.writerResourceId
    writerClientId: identity.outputs.writerClientId
    writerDbRole: identity.outputs.writerName
    readerResourceId: identity.outputs.readerResourceId
    readerClientId: identity.outputs.readerClientId
    readerDbRole: identity.outputs.readerName
    deploySlots: deploySlots
    ollamaBaseUrl: ollamaBaseUrl
    httpsOnly: appServiceHttpsOnly
  }
}

module acr 'modules/acr.bicep' = {
  scope: rg
  name: 'acr'
  params: {
    name: n.acr
    location: acrLocation
    tags: tags
    writerPrincipalId: identity.outputs.writerPrincipalId
    readerPrincipalId: identity.outputs.readerPrincipalId
    logAnalyticsId: monitoring.outputs.logAnalyticsId
  }
}

// ---------------------------------------------------------------------------
// Outputs (consumed by scripts/deploy.sh)
// ---------------------------------------------------------------------------

output resourceGroupName     string = rg.name
output subscriptionId        string = subscription().subscriptionId
output appServiceName        string = n.app
output appInsightsName       string = n.insights
output appServiceUrl         string = 'https://${appService.outputs.defaultHostname}'
// Writer/reader managed-identity principal IDs and their PostgreSQL role
// names. Post-deploy bootstrap (scripts/grant-postgres-aad-admin.sh) grants
// CRUD to the writer role and SELECT-only to the reader role. The reader
// values are empty when ``deploySlots = false``.
output writerPrincipalId     string = identity.outputs.writerPrincipalId
output writerDbRole          string = identity.outputs.writerName
output readerPrincipalId     string = identity.outputs.readerPrincipalId
output readerDbRole          string = identity.outputs.readerName
output acrName               string = n.acr
output acrLoginServer        string = acr.outputs.loginServer
output postgresFqdn          string = postgres.outputs.fqdn
