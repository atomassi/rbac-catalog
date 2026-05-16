#!/usr/bin/env bash
# ============================================================================
# grant-postgres-aad-admin.sh
# ============================================================================
# Configures Entra ID auth on PostgreSQL so the App Service managed identity
# can connect without a password.
#
# Reads resource names from `.deploy-outputs.json` (written by deploy.sh).
# Idempotent — safe to re-run.
#
# Usage:
#   ./scripts/grant-postgres-aad-admin.sh
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILDOUT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$BUILDOUT_DIR"

OUTPUTS_FILE=".deploy-outputs.json"
if [[ ! -f "$OUTPUTS_FILE" ]]; then
  echo "Run scripts/deploy.sh first — $OUTPUTS_FILE not found." >&2
  exit 1
fi

command -v jq    >/dev/null || { echo "jq is required";    exit 1; }
command -v psql  >/dev/null || { echo "psql is required (brew install libpq && brew link --force libpq)"; exit 1; }

# ---------------------------------------------------------------------------
# Resolve deployment outputs
# ---------------------------------------------------------------------------

RG_NAME=$(jq -r            '.resourceGroupName.value'     "$OUTPUTS_FILE")
APP_NAME=$(jq -r           '.appServiceName.value'        "$OUTPUTS_FILE")
APP_OID=$(jq -r            '.appServicePrincipalId.value' "$OUTPUTS_FILE")
PG_FQDN=$(jq -r            '.postgresFqdn.value'          "$OUTPUTS_FILE")
PG_SERVER="${PG_FQDN%%.*}"
PG_DB="azurerbac"

CURRENT_UPN=$(az ad signed-in-user show --query userPrincipalName -o tsv)
CURRENT_OID=$(az ad signed-in-user show --query id -o tsv)

echo "Resource group  : $RG_NAME"
echo "PG server       : $PG_SERVER"
echo "Database        : $PG_DB"
echo "App Service MI  : $APP_NAME (oid=$APP_OID)"
echo

# ---------------------------------------------------------------------------
# 1. Make current user the PG AAD admin (so we can run psql)
# ---------------------------------------------------------------------------

echo "→ Adding $CURRENT_UPN as PG AAD admin..."
az postgres flexible-server ad-admin create \
  --resource-group "$RG_NAME" \
  --server-name "$PG_SERVER" \
  --display-name "$CURRENT_UPN" \
  --object-id "$CURRENT_OID" \
  --type User \
  --output none 2>/dev/null || true   # ignore "already exists"

# ---------------------------------------------------------------------------
# 2. Temporarily allow this machine through the PG firewall
# ---------------------------------------------------------------------------
# The rule uses a stable name so re-runs overwrite (rather than accumulate)
# the entry, and a trap removes it on exit so we don't leave the firewall
# permanently open.

MY_IP=$(curl -fsSL https://api.ipify.org)
FW_RULE_NAME="deploy-shell-temp"

cleanup_firewall_rule() {
  echo "→ Removing temporary PG firewall rule $FW_RULE_NAME..."
  az postgres flexible-server firewall-rule delete \
    --resource-group "$RG_NAME" \
    --name "$PG_SERVER" \
    --rule-name "$FW_RULE_NAME" \
    --yes \
    --output none 2>/dev/null || true
}
trap cleanup_firewall_rule EXIT

echo "→ Allowing $MY_IP through PG firewall (rule: $FW_RULE_NAME)..."
az postgres flexible-server firewall-rule create \
  --resource-group "$RG_NAME" \
  --name "$PG_SERVER" \
  --rule-name "$FW_RULE_NAME" \
  --start-ip-address "$MY_IP" \
  --end-ip-address   "$MY_IP" \
  --output none

# ---------------------------------------------------------------------------
# 3. Run the DDL via psql, using an AAD token as the password
# ---------------------------------------------------------------------------

echo "→ Granting CONNECT/USAGE/CRUD on $PG_DB to the App Service MI..."

PG_TOKEN=$(az account get-access-token --resource-type oss-rdbms --query accessToken -o tsv)

PGPASSWORD="$PG_TOKEN" psql \
  "host=$PG_FQDN port=5432 dbname=$PG_DB user=$CURRENT_UPN sslmode=require" \
  -v ON_ERROR_STOP=1 \
  -v APP_MI_NAME="$APP_NAME" \
  -v APP_MI_OID="$APP_OID" \
  <<'SQL'
\set role_name :APP_MI_NAME
\set role_oid  :APP_MI_OID

-- Create the Entra ID-mapped PG role (idempotent).
-- pgaadauth_create_principal_with_oid is Azure's helper; args:
--   (rolename, objectId, principalType ∈ {'user','group','service'}, isAdmin, isMfa)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role_name') THEN
    PERFORM pgaadauth_create_principal_with_oid(
      :'role_name', :'role_oid', 'service', false, false
    );
  END IF;
END
$$;

GRANT CONNECT ON DATABASE azurerbac TO :"role_name";
GRANT USAGE   ON SCHEMA   public    TO :"role_name";

GRANT SELECT, INSERT, UPDATE, DELETE
  ON ALL TABLES    IN SCHEMA public TO :"role_name";

GRANT USAGE, SELECT
  ON ALL SEQUENCES IN SCHEMA public TO :"role_name";

-- Future tables (the app creates its own on first start)
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES    TO :"role_name";
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT                  ON SEQUENCES TO :"role_name";
SQL

echo
echo "✓ Done. Restart the App Service to refresh its DB connection pool:"
echo "    az webapp restart -n $APP_NAME -g $RG_NAME"
