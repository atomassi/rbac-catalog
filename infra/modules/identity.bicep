// ============================================================================
// Module: shared user-assigned managed identity
// ============================================================================
// ONE identity is used by BOTH the App Service (web) and the Container Apps
// scan Jobs. A single identity maps to a single PostgreSQL role that owns the
// schema, so there is no "who created the tables first" ownership race and no
// cross-grant gymnastics. The same identity also pulls the image from ACR.
// ============================================================================

metadata description = 'Shared user-assigned managed identity for the web app and scan Jobs.'

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
