// ============================================================================
// Dev profile — cheaper SKUs, no slots.
// Uses the same env-var-driven values as prod.bicepparam.
// ============================================================================

using '../main.bicep'

param environmentName    = 'dev'
param resourceGroupName  = readEnvironmentVariable('RG_NAME',   'myapp-dev-rg')
param baseName           = readEnvironmentVariable('BASE_NAME', 'myappdev')
param location           = readEnvironmentVariable('LOCATION',  'westeurope')
// B2 (2 cores) is the minimum: the web server and the background scan worker
// share the plan, and B1's single core starves the event loop during scans
// (every route times out). B3 / P0v3 are ideal.
param appServicePlanSku  = 'B2'

// (deploySlots defaults to false)

param postgresAdminPassword = readEnvironmentVariable('PG_ADMIN_PASSWORD', '')
param ollamaBaseUrl         = readEnvironmentVariable('OLLAMA_BASE_URL',   '')
