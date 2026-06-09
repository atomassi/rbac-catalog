// ============================================================================
// Module: Container Apps Environment + scan cron Jobs (web stays on App Service)
// ============================================================================
// The web tier runs on App Service (modules/appservice.bicep). This module
// adds ONLY the background scans, decoupled from the web image, as Container
// Apps *Jobs*:
//   * role-scan       — cron, every 2h by default.
//   * operations-scan — cron, daily by default.
//
// Both run the repo image with an overridden command
// (``python -m rbaccatalog.backgroundjobs.scan_once <name>``), scale to zero
// between runs (you pay only for the seconds a scan actually runs), and are
// single-writer by construction (parallelism = 1, replicaCompletionCount = 1).
// They replace the worker's APScheduler loop that used to be co-located in the
// App Service container.
//
// Both Jobs use the SAME shared user-assigned managed identity as the web app
// (passed in from main.bicep). That identity already has AcrPull (granted in
// acr.bicep) and maps to the single PostgreSQL role created by
// scripts/grant-postgres-aad-admin.sh.
// ============================================================================

metadata description = 'Container Apps environment with cron Jobs for the role/operations scans (web tier remains on App Service).'

@description('Container Apps Environment name.')
param environmentResourceName string

@description('Scan user-assigned managed identity (read-write). Its name is also the PostgreSQL role / MSI_DB_USER.')
param scanIdentityName string

@description('Resource ID of the scan user-assigned managed identity.')
param scanIdentityId string

@description('Client ID of the scan user-assigned managed identity (AZURE_CLIENT_ID).')
param scanIdentityClientId string

@description('Role-scan Job resource name.')
param roleScanJobName string

@description('Operations-scan Job resource name.')
param operationsScanJobName string

@description('Azure region.')
param location string

@description('Resource tags.')
param tags object

@description('ACR login server (e.g. myappregistry.azurecr.io).')
param acrLoginServer string

@description('''Image tag to run. Defaults to 'latest' for the initial/bootstrap
deploy; the release pipeline (.github/workflows/deploy.yml) re-pins both Jobs to
the exact versioned tag that passed staging smoke tests via `az containerapp job
update`, so scheduled scans run a known-good, rollback-able image. Mirrors
appservice.bicep.''')
param imageTag string = 'latest'

@description('Log Analytics workspace name (same resource group) for the environment log sink.')
param logAnalyticsName string

@description('Application Insights connection string.')
param appInsightsConnectionString string

@description('PostgreSQL fully-qualified domain name.')
param postgresHost string

@description('PostgreSQL database name.')
param postgresDatabase string = 'azurerbac'

@description('Cron expression for the role scan (UTC). Default: every 2 hours, on the hour.')
param roleScanCron string = '0 */2 * * *'

@description('Cron expression for the operations scan (UTC). Default: daily at 00:00.')
param operationsScanCron string = '0 0 * * *'

@description('vCPU per scan replica.')
param cpu string = '0.5'

@description('Memory per scan replica.')
param memory string = '1Gi'

@description('Per-run timeout for scan jobs, in seconds.')
param jobReplicaTimeout int = 600

var fullImage = '${acrLoginServer}/rbaccatalog:${imageTag}'

// Database via managed identity / passwordless auth, matching appservice.bicep.
var dbEnv = [
  { name: 'USE_MANAGED_IDENTITY', value: 'true' }
  { name: 'MSI_DB_HOST', value: postgresHost }
  { name: 'MSI_DB_PORT', value: '5432' }
  { name: 'MSI_DB_NAME', value: postgresDatabase }
  // Select the shared user-assigned identity (no system-assigned MI here).
  { name: 'AZURE_CLIENT_ID', value: scanIdentityClientId }
]

var observabilityEnv = [
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
  // Always 'production' (a recognized runtime env), never the infra naming
  // token (e.g. 'dev'), which the app coerces to 'local' — disabling App
  // Insights and re-enabling file logging.
  { name: 'APP_ENVIRONMENT_NAME', value: 'production' }
  { name: 'LOG_LEVEL', value: 'INFO' }
]

resource laws 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: logAnalyticsName
}

// ---------------------------------------------------------------------------
// Managed environment (shared by both jobs). No base fee in Consumption; you
// pay only for the seconds each scan runs.
// ---------------------------------------------------------------------------
resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentResourceName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: laws.properties.customerId
        sharedKey: laws.listKeys().primarySharedKey
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Scan Jobs — cron-triggered, scale-to-zero, single-writer by construction.
// ---------------------------------------------------------------------------
// NOTE: scan_once honours the job's ``enabled`` flag (Worker.run_job returns
// early when disabled), so each Job MUST set its own ROLE_SCAN_ENABLED /
// OPERATIONS_SCAN_ENABLED to ``true`` — otherwise the run is a silent no-op.
resource roleScanJob 'Microsoft.App/jobs@2024-03-01' = {
  name: roleScanJobName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${scanIdentityId}': {}
    }
  }
  properties: {
    environmentId: env.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: jobReplicaTimeout
      // No blind platform retry: Container Apps can't tell a transient 429
      // from a permanent 403, and the next cron tick is the natural recovery
      // for transient failures. A failed run surfaces as a Failed job run
      // (scan_once exits non-zero) for alerting.
      replicaRetryLimit: 0
      scheduleTriggerConfig: {
        cronExpression: roleScanCron
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [
        { server: acrLoginServer, identity: scanIdentityId }
      ]
    }
    template: {
      containers: [
        {
          name: 'role-scan'
          image: fullImage
          command: [ 'python', '-m', 'rbaccatalog.backgroundjobs.scan_once' ]
          args: [ 'role-scan' ]
          resources: {
            cpu: json(cpu)
            memory: memory
          }
          env: concat(dbEnv, observabilityEnv, [
            { name: 'MSI_DB_USER', value: scanIdentityName }
            { name: 'ROLE_SCAN_ENABLED', value: 'true' }
          ])
        }
      ]
    }
  }
}

resource operationsScanJob 'Microsoft.App/jobs@2024-03-01' = {
  name: operationsScanJobName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${scanIdentityId}': {}
    }
  }
  properties: {
    environmentId: env.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: jobReplicaTimeout
      // No blind platform retry: Container Apps can't tell a transient 429
      // from a permanent 403, and the next cron tick is the natural recovery
      // for transient failures. A failed run surfaces as a Failed job run
      // (scan_once exits non-zero) for alerting.
      replicaRetryLimit: 0
      scheduleTriggerConfig: {
        cronExpression: operationsScanCron
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [
        { server: acrLoginServer, identity: scanIdentityId }
      ]
    }
    template: {
      containers: [
        {
          name: 'operations-scan'
          image: fullImage
          command: [ 'python', '-m', 'rbaccatalog.backgroundjobs.scan_once' ]
          args: [ 'operations-scan' ]
          resources: {
            cpu: json(cpu)
            memory: memory
          }
          env: concat(dbEnv, observabilityEnv, [
            { name: 'MSI_DB_USER', value: scanIdentityName }
            { name: 'OPERATIONS_SCAN_ENABLED', value: 'true' }
          ])
        }
      ]
    }
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------
output environmentId string = env.id
