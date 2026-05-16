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
# Slot principal IDs are emitted only when ``deploySlots = true``. Treat
# missing/empty values as "no slot" so a slotless deployment Just Works.
STAGING_OID=$(jq -r        '.stagingSlotPrincipalId.value // empty' "$OUTPUTS_FILE")
PPE_OID=$(jq -r            '.ppeSlotPrincipalId.value     // empty' "$OUTPUTS_FILE")
PG_FQDN=$(jq -r            '.postgresFqdn.value'          "$OUTPUTS_FILE")
PG_SERVER="${PG_FQDN%%.*}"
PG_DB="azurerbac"

CURRENT_UPN=$(az ad signed-in-user show --query userPrincipalName -o tsv)
CURRENT_OID=$(az ad signed-in-user show --query id -o tsv)

echo "Resource group  : $RG_NAME"
echo "PG server       : $PG_SERVER"
echo "Database        : $PG_DB"
echo "App Service MI  : $APP_NAME (oid=$APP_OID)"
[[ -n "$STAGING_OID" ]] && echo "Staging slot MI : ${APP_NAME}/slots/staging (oid=$STAGING_OID)"
[[ -n "$PPE_OID"     ]] && echo "PPE slot MI     : ${APP_NAME}/slots/ppe     (oid=$PPE_OID)"
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
# Each identity (production slot + each deployment slot) gets its own
# pgaadauth principal and the same set of CRUD grants. Slots have their
# own managed identities, so they need their own grants — production's
# grant does NOT cover them.

echo "→ Granting CONNECT/USAGE/CRUD on $PG_DB to each MI..."

PG_TOKEN=$(az account get-access-token --resource-type oss-rdbms --query accessToken -o tsv)

grant_identity() {
  local mi_name="$1" mi_oid="$2"
  echo "  • $mi_name (oid=$mi_oid)"
  PGPASSWORD="$PG_TOKEN" psql \
    "host=$PG_FQDN port=5432 dbname=$PG_DB user=$CURRENT_UPN sslmode=require" \
    -v ON_ERROR_STOP=1 \
    -v APP_MI_NAME="$mi_name" \
    -v APP_MI_OID="$mi_oid" \
    <<'SQL'
\set role_name :APP_MI_NAME
\set role_oid  :APP_MI_OID

-- psql ``:'var'`` expansion does NOT happen inside a ``DO $$ ... $$`` block;
-- the server receives the literal colon-quoted text. Stash the values in a
-- custom GUC at the top-level SELECT (where psql expands them correctly) and
-- read them back inside the block via ``current_setting()``.
SELECT set_config('app.role_name', :'role_name', false);
SELECT set_config('app.role_oid',  :'role_oid',  false);

-- Create the Entra ID-mapped PG role (idempotent).
-- pgaadauth_create_principal_with_oid is Azure's helper; args:
--   (rolename, objectId, principalType ∈ {'user','group','service'}, isAdmin, isMfa)
DO $$
DECLARE
  v_role text := current_setting('app.role_name');
  v_oid  text := current_setting('app.role_oid');
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_role) THEN
    PERFORM pgaadauth_create_principal_with_oid(
      v_role, v_oid, 'service', false, false
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
}

# Production slot — the MSI_DB_USER for production matches the App Service
# name (which is the production identity's MI display name).
grant_identity "$APP_NAME" "$APP_OID"

# Each deployment slot's MI display name is "<app>/slots/<slot>". The app
# uses the same MSI_DB_USER as production, so slot identities map to the
# SAME PG role; we therefore only need to register each slot's OID under
# that role. pgaadauth_create_principal_with_oid is per-role though, so we
# create a per-slot role and let the app discover it via MSI_DB_USER on the
# slot's own slot-specific MSI_DB_USER app setting.
#
# NOTE: To keep the existing single-MSI_DB_USER model working, the slot
# identities need their OWN PG roles. The simplest answer is to use a
# distinct MSI_DB_USER value per slot (set via slot-only app settings in
# the appservice module). For now we register the slot identities under
# slot-named roles so operators can set MSI_DB_USER accordingly.
if [[ -n "$STAGING_OID" ]]; then
  grant_identity "${APP_NAME}-slot-staging" "$STAGING_OID"
fi
if [[ -n "$PPE_OID" ]]; then
  grant_identity "${APP_NAME}-slot-ppe"     "$PPE_OID"
fi

echo
echo "✓ Done. Restart the App Service to refresh its DB connection pool:"
echo "    az webapp restart -n $APP_NAME -g $RG_NAME"
