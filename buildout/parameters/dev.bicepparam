// ============================================================================
// Dev profile — cheaper SKUs, no slots.
// ============================================================================

using '../main.bicep'

param environmentName    = 'dev'
param resourceGroupName  = 'myapp-dev-rg'
param baseName           = 'myappdev'
param appServicePlanSku  = 'B1'

// (deploySlots defaults to false)

param postgresAdminPassword = readEnvironmentVariable('PG_ADMIN_PASSWORD', '')
