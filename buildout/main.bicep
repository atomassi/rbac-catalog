// ============================================================================
// Azure RBAC Catalog — single-file Bicep entry point
// ============================================================================
// Mandatory : App Service + ACR + PostgreSQL (AAD auth) + Log Analytics + App Insights
// Optional  : VNet, Ollama VM, deployment slots, Automation runbooks
//
// Edit parameters/*.bicepparam, then run `./scripts/deploy.sh`.
// ============================================================================

metadata name        = 'azurerbac-buildout'
metadata description = 'Subscription-scoped deployment of the Azure RBAC Catalog (App Service + ACR + PostgreSQL + monitoring, with optional VNet/Ollama VM/slots/automation).'

targetScope = 'subscription'

// ---------------------------------------------------------------------------
// Parameters
// ---------------------------------------------------------------------------

@description('Environment tag (drives naming suffixes and SKU defaults).')
@allowed([ 'prod', 'dev' ])
param environmentName string = 'prod'

@description('Resource group name to create or update.')
param resourceGroupName string = 'myapp-rg'

@description('Primary Azure region (App Service, monitoring, optional VNet/VM).')
param location string = 'westeurope'

@description('Region for the PostgreSQL Flexible Server. Defaults to `location`.')
param postgresLocation string = location

@description('Region for the Azure Container Registry. Defaults to `location`.')
param acrLocation string = location

@description('Base name (3-15 alphanumeric chars). Used as the prefix for every resource name.')
@minLength(3)
@maxLength(15)
param baseName string = 'myapp'

@description('App Service Plan SKU.')
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

@description('Ollama VM size (only used when deployOllamaVm = true).')
param ollamaVmSize string = 'Standard_B2als_v2'

@description('PostgreSQL administrator username (initial bootstrap; the app uses Entra ID after).')
param postgresAdminUser string = 'pgadmin'

@description('PostgreSQL administrator password. 8-128 chars, mixed case + digit + symbol.')
@secure()
param postgresAdminPassword string

@description('SSH public key authorized on the Ollama VM. Required only when deployOllamaVm = true.')
param sshPublicKey string = ''

@description('Public IP allowed to SSH the Ollama VM. Required only when deployOllamaVm = true.')
param adminIpAddress string = ''

@description('Provision the VNet + subnets + Ollama NSG. Required when deployOllamaVm = true.')
param deployVNet bool = false

@description('Provision the Ollama VM (Ubuntu, B2als_v2). Requires deployVNet = true.')
param deployOllamaVm bool = false

@description('Provision staging + ppe deployment slots. Recommended (not required) for blue/green deploys.')
param deploySlots bool = false

@description('Provision Automation Account + housekeeping runbooks (ACR cleanup, PPE shutdown).')
param deployAutomation bool = false

@description('Override the OLLAMA_BASE_URL env var injected into the App Service. Empty = auto-compute from the VM IP, or AI disabled.')
param ollamaBaseUrlOverride string = ''

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
  vnet:       '${baseName}-vnet'
  logs:       '${baseName}-logs'
  insights:   '${baseName}-insights'
  automation: '${baseName}-automation'
  ollama:     '${baseName}-ollama'
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

module network 'modules/network.bicep' = if (deployVNet) {
  scope: rg
  name: 'network'
  params: { vnetName: n.vnet, location: location, tags: tags, adminIpAddress: adminIpAddress }
}

module monitoring 'modules/monitoring.bicep' = {
  scope: rg
  name: 'monitoring'
  params: { logAnalyticsName: n.logs, appInsightsName: n.insights, location: location, tags: tags }
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

module ollamaVm 'modules/ollama-vm.bicep' = if (deployOllamaVm && deployVNet) {
  scope: rg
  name: 'ollama-vm'
  params: {
    vmName: n.ollama
    location: location
    tags: tags
    vmSize: ollamaVmSize
    sshPublicKey: sshPublicKey
    subnetId: deployVNet ? network!.outputs.ollamaSubnetId : ''
  }
}

// OLLAMA_BASE_URL: explicit override wins; otherwise auto-compute from the VM IP.
var ollamaBaseUrl = !empty(ollamaBaseUrlOverride)
  ? ollamaBaseUrlOverride
  : (deployOllamaVm && deployVNet ? 'http://${ollamaVm!.outputs.privateIp}:11434' : '')

module appService 'modules/appservice.bicep' = {
  scope: rg
  name: 'appservice'
  params: {
    appName: n.app
    planName: n.plan
    location: location
    tags: tags
    sku: appServicePlanSku
    acrLoginServer: '${n.acr}.azurecr.io'
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    logAnalyticsId: monitoring.outputs.logAnalyticsId
    postgresHost: postgres.outputs.fqdn
    // PG role created by scripts/grant-postgres-aad-admin.sh matches the App
    // Service name (its MI display name).
    postgresUser: n.app
    appServiceSubnetId: deployVNet ? network!.outputs.appServiceSubnetId : ''
    environmentName: environmentName
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
    appServicePrincipalId: appService.outputs.appServicePrincipalId
    logAnalyticsId: monitoring.outputs.logAnalyticsId
  }
}

module automation 'modules/automation.bicep' = if (deployAutomation) {
  scope: rg
  name: 'automation'
  params: {
    accountName: n.automation
    location: location
    tags: tags
    acrName: n.acr
    appServiceName: n.app
    runPpeShutdown: deploySlots
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
output appServicePrincipalId string = appService.outputs.appServicePrincipalId
output acrName               string = n.acr
output acrLoginServer        string = acr.outputs.loginServer
output postgresFqdn          string = postgres.outputs.fqdn
output ollamaPrivateIp       string = deployOllamaVm && deployVNet ? ollamaVm!.outputs.privateIp : ''
