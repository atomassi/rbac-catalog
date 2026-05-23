// ============================================================================
// Production profile — deployment slots ON.
//
// All values that vary per deployment (resource group, name prefix, region,
// password, optional Ollama endpoint) come from environment variables so the
// same .bicepparam works for everyone:
//
//   export RG_NAME="myapp-rg"
//   export BASE_NAME="myapp"
//   export LOCATION="westeurope"
//   export PG_ADMIN_PASSWORD="<strong-password>"
//   # optional: export OLLAMA_BASE_URL="http://my-ollama:11434"
//
// See README "Step 1" for the full list.
// ============================================================================

using '../main.bicep'

param resourceGroupName = readEnvironmentVariable('RG_NAME',   'myapp-rg')
param baseName          = readEnvironmentVariable('BASE_NAME', 'myapp')
param location          = readEnvironmentVariable('LOCATION',  'westeurope')

param deploySlots = true

param postgresAdminPassword = readEnvironmentVariable('PG_ADMIN_PASSWORD', '')

// Optional external Ollama-compatible endpoint. Empty (the default) disables
// LLM-backed recommendations; the catalog and rule-based recommender still
// work. The infra does NOT provision Ollama itself.
param ollamaBaseUrl = readEnvironmentVariable('OLLAMA_BASE_URL', '')
