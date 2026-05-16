// ============================================================================
// Production profile.
// Defaults: VNet + deployment slots ON; Ollama VM and Automation OFF (opt-in).
// Customize the two name values below; flip the optional flags as needed,
// or override per-deploy: --parameters deployOllamaVm=true deployAutomation=true
// ============================================================================

using '../main.bicep'

// --- Edit these for your deployment ---------------------------------------
param resourceGroupName = 'myapp-rg'
param baseName          = 'myapp'

// --- Optional features ----------------------------------------------------
// VNet + slots are part of the recommended production baseline.
param deployVNet       = true
param deploySlots      = true

// Ollama VM (~$30/mo) and Automation Account are opt-in extras. Enable
// either by setting to true here, or per-deploy via --parameters.
param deployOllamaVm   = false
param deployAutomation = false

// --- Secrets (read from environment via `source .env`) --------------------
param postgresAdminPassword = readEnvironmentVariable('PG_ADMIN_PASSWORD', '')
param sshPublicKey          = readEnvironmentVariable('SSH_PUBLIC_KEY',    '')
param adminIpAddress        = readEnvironmentVariable('ADMIN_IP',          '')
