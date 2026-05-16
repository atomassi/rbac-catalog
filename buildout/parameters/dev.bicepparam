// ============================================================================
// Dev profile — mandatory resources only, cheaper SKUs.
// ============================================================================

using '../main.bicep'

param environmentName    = 'dev'
param resourceGroupName  = 'myapp-dev-rg'
param baseName           = 'myappdev'
param appServicePlanSku  = 'B1'

// (deployVNet/deployOllamaVm/deploySlots/deployAutomation default to false)

param postgresAdminPassword = readEnvironmentVariable('PG_ADMIN_PASSWORD', '')
