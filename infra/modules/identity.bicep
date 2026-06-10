// ============================================================================
// Module: a single user-assigned managed identity
// ============================================================================
// Creates ONE user-assigned managed identity per invocation. main.bicep calls
// this module twice — once for the web tier (SELECT-only) and once for the scan
// Jobs (read-write schema owner) — so each identity maps to exactly one
// PostgreSQL role and there is no "who created the tables first" ownership race.
// The identity name doubles as the PostgreSQL role name (MSI_DB_USER), and the
// identity is also granted AcrPull to pull the image.
// ============================================================================

metadata description = 'A single user-assigned managed identity (one per invocation; e.g. web or scan).'

@description('Identity name. Also used as the PostgreSQL role name (MSI_DB_USER).')
param name string

@description('Azure region.')
param location string

@description('Resource tags.')
param tags object

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: name
  location: location
  tags: tags
}

output id string = identity.id
output name string = identity.name
output clientId string = identity.properties.clientId
output principalId string = identity.properties.principalId
