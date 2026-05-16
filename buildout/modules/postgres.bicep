// ============================================================================
// Module: PostgreSQL Flexible Server
// ============================================================================
// Entra ID auth is always on. Password auth is parameterizable — keep it on
// during the initial bootstrap (the admin password is needed to provision
// the AAD-mapped role), then set `enablePasswordAuth = false` to remove the
// password attack surface.
// Extensions `vector` and `pg_trgm` are enabled for AI embeddings and
// fuzzy text search.
// ============================================================================

metadata description = 'PostgreSQL Flexible Server with Entra ID auth, pgvector + pg_trgm extensions, and optional diagnostic settings.'

@description('PostgreSQL server name. Globally unique.')
param serverName string

@description('Database created on the server.')
param databaseName string = 'azurerbac'

@description('Azure region.')
param location string

@description('Resource tags.')
param tags object

@description('Administrator username (used for the initial bootstrap; the app uses Entra ID after).')
param administratorLogin string

@description('Administrator password.')
@secure()
param administratorPassword string

@description('SKU name (e.g. Standard_B1ms).')
param skuName string

@description('SKU tier.')
@allowed([ 'Burstable', 'GeneralPurpose', 'MemoryOptimized' ])
param tier string

@description('Storage size in GB.')
param storageSizeGB int

@description('PostgreSQL major version.')
param version string

@description('Enable password auth in addition to Entra ID. Required during the initial bootstrap so the password admin can be used. After the AAD-mapped role exists and the app connects via MI, set this to false to remove the password attack surface.')
param enablePasswordAuth bool = true

@description('Allow all Azure-resident services to reach the server (firewall rule 0.0.0.0/0.0.0.0). This is convenient but exposes the server to every Azure tenant. Set to false and use VNet integration / Private Endpoint for a hardened deployment.')
param allowAllAzureServices bool = true

@description('Log Analytics workspace ID. When non-empty, audit logs and metrics are forwarded for security monitoring.')
param logAnalyticsId string = ''

resource server 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: serverName
  location: location
  tags: tags
  sku: { name: skuName, tier: tier }
  properties: {
    version: version
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorPassword
    storage: { storageSizeGB: storageSizeGB, autoGrow: 'Disabled' }
    backup: { backupRetentionDays: 7, geoRedundantBackup: 'Disabled' }
    highAvailability: { mode: 'Disabled' }
    network: { publicNetworkAccess: 'Enabled' }
    authConfig: {
      activeDirectoryAuth: 'Enabled'
      passwordAuth: enablePasswordAuth ? 'Enabled' : 'Disabled'
      tenantId: subscription().tenantId
    }
  }
}

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: server
  name: databaseName
  properties: { charset: 'UTF8', collation: 'en_US.utf8' }
}

// SECURITY: this rule (start=0.0.0.0, end=0.0.0.0) is the Azure-special
// "Allow all Azure services" toggle. It permits inbound traffic from EVERY
// Azure tenant — not just yours. Acceptable for dev/demo, NOT for production.
// Disable by setting `allowAllAzureServices = false` and use VNet integration
// (server in private mode + private endpoint) instead.
resource allowAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = if (allowAllAzureServices) {
  parent: server
  name: 'AllowAllAzureServicesAndResourcesWithinAzureIps'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
}

// Extensions used by the app:
//   * vector  — AI embeddings storage / similarity search.
//   * pg_trgm — fuzzy text search.
resource extensions 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2024-08-01' = {
  parent: server
  name: 'azure.extensions'
  properties: { value: 'VECTOR,PG_TRGM', source: 'user-override' }
}

// Audit + metrics to Log Analytics (security monitoring + forensics).
resource diagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = if (!empty(logAnalyticsId)) {
  name: 'PostgreSQLLogs'
  scope: server
  properties: {
    workspaceId: logAnalyticsId
    logs: [
      { categoryGroup: 'allLogs', enabled: true }
    ]
    metrics: [ { category: 'AllMetrics', enabled: true } ]
  }
}

output id string = server.id
output name string = server.name
output fqdn string = server.properties.fullyQualifiedDomainName
