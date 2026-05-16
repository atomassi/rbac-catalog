// ============================================================================
// Production profile.
// Defaults: deployment slots ON.
// Customize the two name values below; flip the optional flags as needed.
//
// Out of scope by design (set ``OLLAMA_BASE_URL`` as an app setting to wire
// up an external Ollama endpoint, or leave it empty to disable AI features):
//   * Ollama VM           — not provisioned. Bring your own endpoint.
//   * VNet integration    — App Service uses the public PostgreSQL endpoint.
//   * Automation Account  — runbook content was never published; not useful.
// ============================================================================

using '../main.bicep'

// --- Edit these for your deployment ---------------------------------------
param resourceGroupName = 'myapp-rg'
param baseName          = 'myapp'

// --- Optional features ----------------------------------------------------
param deploySlots = true

// --- Optional external endpoints ------------------------------------------
// Set OLLAMA_BASE_URL via .env (e.g. http://your-ollama-host:11434) or leave
// empty to disable AI features.
param ollamaBaseUrl = readEnvironmentVariable('OLLAMA_BASE_URL', '')

// --- Secrets (read from environment via `source .env`) --------------------
param postgresAdminPassword = readEnvironmentVariable('PG_ADMIN_PASSWORD', '')
