// ============================================================================
// Dev profile — cheaper SKUs, no slots.
// Uses the same env-var-driven values as prod.bicepparam.
// ============================================================================

using '../main.bicep'

param environmentName    = 'dev'
param resourceGroupName  = readEnvironmentVariable('RG_NAME',   'myapp-dev-rg')
param baseName           = readEnvironmentVariable('BASE_NAME', 'myappdev')
param location           = readEnvironmentVariable('LOCATION',  'westeurope')
param appServicePlanSku  = 'B1'

// (deploySlots defaults to false)

param postgresAdminPassword = readEnvironmentVariable('PG_ADMIN_PASSWORD', '')
param ollamaBaseUrl         = readEnvironmentVariable('OLLAMA_BASE_URL',   '')
