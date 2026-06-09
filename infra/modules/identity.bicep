// ============================================================================
// Module: User-assigned managed identities (writer + reader)
// ============================================================================
// Two identities split by database privilege, instead of one system-assigned
// identity per slot:
//   * writer — attached to the production slot. Full CRUD on PostgreSQL; the
//              single catalog writer (scans run only on production).
//   * reader — attached to the staging + ppe slots. SELECT-only on PostgreSQL,
//              so a non-production slot physically cannot write to (or race)
//              the catalog regardless of its scan-enable env flags.
//
// The reader is shared across all non-production slots because they are
// privilege-identical; there is no reason to distinguish staging from ppe at
// the identity layer. It is created only when slots are deployed.
// ============================================================================

metadata description = 'Writer + reader user-assigned managed identities for the App Service production slot and (optionally) its deployment slots.'

@description('Base name; identities are named <baseName>-writer / <baseName>-reader.')
param baseName string

@description('Azure region.')
param location string

@description('Resource tags.')
param tags object

@description('Provision the reader identity for the staging + ppe slots.')
param deploySlots bool

resource writer 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${baseName}-writer'
  location: location
  tags: tags
}

resource reader 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = if (deploySlots) {
  name: '${baseName}-reader'
  location: location
  tags: tags
}

output writerResourceId  string = writer.id
output writerPrincipalId string = writer.properties.principalId
output writerClientId    string = writer.properties.clientId
output writerName        string = writer.name

// Empty strings when slots are not deployed so callers can pass the outputs
// through unconditionally; the ``!.`` operator guards the access path.
output readerResourceId  string = deploySlots ? reader!.id                      : ''
output readerPrincipalId string = deploySlots ? reader!.properties.principalId  : ''
output readerClientId    string = deploySlots ? reader!.properties.clientId     : ''
output readerName        string = deploySlots ? reader!.name                    : ''
