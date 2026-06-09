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
INFRA_DIR="$(dirname "$SCRIPT_DIR")"
cd "$INFRA_DIR"

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

RG_NAME=$(jq -r            '.resourceGroupName.value'      "$OUTPUTS_FILE")
APP_NAME=$(jq -r           '.appServiceName.value'         "$OUTPUTS_FILE")
# Two least-privilege identities, each mapped to a PostgreSQL role:
#   * web  → SELECT-only (the web tier never writes).
#   * scan → owner / read-write (the scan Jobs create + populate the schema).
WEB_ID_NAME=$(jq -r        '.webIdentityName.value'        "$OUTPUTS_FILE")
WEB_ID_OID=$(jq -r         '.webIdentityPrincipalId.value' "$OUTPUTS_FILE")
SCAN_ID_NAME=$(jq -r       '.scanIdentityName.value'       "$OUTPUTS_FILE")
SCAN_ID_OID=$(jq -r        '.scanIdentityPrincipalId.value' "$OUTPUTS_FILE")
PG_FQDN=$(jq -r            '.postgresFqdn.value'           "$OUTPUTS_FILE")
PG_SERVER="${PG_FQDN%%.*}"
PG_DB="azurerbac"

CURRENT_UPN=$(az ad signed-in-user show --query userPrincipalName -o tsv)
CURRENT_OID=$(az ad signed-in-user show --query id -o tsv)

echo "Resource group  : $RG_NAME"
echo "PG server       : $PG_SERVER"
echo "Database        : $PG_DB"
echo "Web MI (SELECT) : $WEB_ID_NAME (oid=$WEB_ID_OID)"
echo "Scan MI (owner) : $SCAN_ID_NAME (oid=$SCAN_ID_OID)"
echo

# ---------------------------------------------------------------------------
# 1. Make current user the PG Entra admin (so we can run psql)
# ---------------------------------------------------------------------------
# The az CLI subcommand name has changed across versions:
#   * az postgres flexible-server ad-admin               (old)
#   * az postgres flexible-server microsoft-entra-admin  (current)
# Detect which one this CLI supports and use it. Errors other than the
# idempotent "already exists" case MUST surface — silently swallowing them
# (the original behaviour) led to the script appearing to succeed while
# leaving no admin configured, which manifested 20s later as a confusing
# ``password authentication failed`` from psql.

if az postgres flexible-server microsoft-entra-admin --help >/dev/null 2>&1; then
  PG_ADMIN_CMD="microsoft-entra-admin"
elif az postgres flexible-server ad-admin --help >/dev/null 2>&1; then
  PG_ADMIN_CMD="ad-admin"
else
  echo "ERROR: az CLI has neither 'microsoft-entra-admin' nor 'ad-admin' subcommand." >&2
  echo "       Update the Azure CLI: az upgrade" >&2
  exit 1
fi

echo "→ Adding $CURRENT_UPN as PG Entra admin (via az ... $PG_ADMIN_CMD)..."
admin_err=$(az postgres flexible-server "$PG_ADMIN_CMD" create \
  --resource-group "$RG_NAME" \
  --server-name "$PG_SERVER" \
  --display-name "$CURRENT_UPN" \
  --object-id "$CURRENT_OID" \
  --type User \
  --output none 2>&1) || admin_rc=$?

if [[ "${admin_rc:-0}" -ne 0 ]]; then
  if grep -qiE "already exists|AlreadyExists" <<<"$admin_err"; then
    echo "  ✓ admin already exists"
  else
    echo "ERROR: failed to create Entra admin on PG server:" >&2
    echo "$admin_err" >&2
    exit 1
  fi
fi

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
# Two least-privilege roles, no ownership race: the scan role owns the schema
# (it is the only writer / the only one that runs create_all), and the web role
# gets SELECT plus a one-directional default-SELECT on future scan-owned tables.

echo "→ Granting PostgreSQL roles (scan=owner, web=SELECT-only) on $PG_DB..."

PG_TOKEN=$(az account get-access-token --resource-type oss-rdbms --query accessToken -o tsv)

# The AAD-admin assignment created above propagates server-side
# asynchronously. Block until psql can actually connect (against the
# ``postgres`` system database, which is always present) before issuing
# any DDL — otherwise the first call fails with ``password authentication
# failed`` and leaves the operator with no AAD-mapped roles.
echo "→ Waiting for AAD admin assignment to propagate..."
for attempt in $(seq 1 24); do
  if PGPASSWORD="$PG_TOKEN" psql \
       "host=$PG_FQDN port=5432 dbname=postgres user=$CURRENT_UPN sslmode=require connect_timeout=5" \
       -v ON_ERROR_STOP=1 -c "SELECT 1;" >/dev/null 2>&1; then
    echo "  ✓ AAD admin ready (attempt $attempt)"
    break
  fi
  if (( attempt == 24 )); then
    echo "  ERROR: AAD admin did not propagate within ~120s." >&2
    echo "         Retry by re-running this script." >&2
    exit 1
  fi
  sleep 5
done

# How this works:
#   * The ``pgaadauth_*`` helpers live ONLY in the ``postgres`` system
#     database. Azure pre-installs them there and BLOCKS installing the
#     extension into user databases (it would expose internal
#     management functions to non-admin owners).
#   * So we:
#       1. connect to ``postgres`` to ``pgaadauth_create_principal_with_oid``
#          — this creates a server-level role mapped to the Entra ID OID.
#       2. connect to ``$PG_DB`` to issue the per-database CONNECT / USAGE /
#          CRUD grants.
#
# Both calls run as the AAD admin (the human signed in to ``az``).

create_principal() {
  local mi_name="$1" mi_oid="$2"
  echo "  • create principal: $mi_name (oid=$mi_oid)"
  PGPASSWORD="$PG_TOKEN" psql \
    "host=$PG_FQDN port=5432 dbname=postgres user=$CURRENT_UPN sslmode=require" \
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

-- ``pgaadauth_create_principal_with_oid`` signature:
--   (rolename text, objectid text, objecttype text, isadmin boolean, ismfa boolean)
-- The literal ``'service'`` resolves to ``unknown`` until cast.
DO $$
DECLARE
  v_role text := current_setting('app.role_name');
  v_oid  text := current_setting('app.role_oid');
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_role) THEN
    PERFORM pgaadauth_create_principal_with_oid(
      v_role, v_oid, 'service'::text, false, false
    );
  END IF;
END
$$;
SQL
}

grant_scan_in_db() {
  local mi_name="$1"
  echo "  • grant on $PG_DB (owner/read-write): $mi_name"
  PGPASSWORD="$PG_TOKEN" psql \
    "host=$PG_FQDN port=5432 dbname=$PG_DB user=$CURRENT_UPN sslmode=require" \
    -v ON_ERROR_STOP=1 \
    -v APP_MI_NAME="$mi_name" \
    <<'SQL'
\set role_name :APP_MI_NAME

GRANT CONNECT ON DATABASE azurerbac TO :"role_name";

-- USAGE resolves names in ``public``; CREATE lets this role own the schema
-- (the scan Jobs' SQLAlchemy ``create_all`` runs first and is the source of
-- truth for the schema).
GRANT USAGE, CREATE ON SCHEMA public TO :"role_name";

-- Pre-existing objects (idempotent re-runs): full CRUD. Future objects need no
-- handling — this role owns everything it creates via ensure_db.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES    IN SCHEMA public TO :"role_name";
GRANT USAGE, SELECT                  ON ALL SEQUENCES IN SCHEMA public TO :"role_name";
SQL
}

grant_web_in_db() {
  local web_name="$1" scan_name="$2"
  echo "  • grant on $PG_DB (SELECT-only): $web_name"
  PGPASSWORD="$PG_TOKEN" psql \
    "host=$PG_FQDN port=5432 dbname=$PG_DB user=$CURRENT_UPN sslmode=require" \
    -v ON_ERROR_STOP=1 \
    -v WEB_NAME="$web_name" \
    -v SCAN_NAME="$scan_name" \
    <<'SQL'
\set web_name  :WEB_NAME
\set scan_name :SCAN_NAME

GRANT CONNECT ON DATABASE azurerbac TO :"web_name";

-- Read-only: USAGE to resolve names, SELECT on data. No CREATE, no write.
GRANT USAGE ON SCHEMA public TO :"web_name";
GRANT SELECT ON ALL TABLES IN SCHEMA public TO :"web_name";

-- Future tables the scan role creates must be auto-readable by the web role.
-- ALTER DEFAULT PRIVILEGES FOR ROLE <scan> requires membership in <scan>, so
-- the AAD admin (running this) joins it first. One-directional (scan → web
-- SELECT); the scan role never needs anything from web.
GRANT :"scan_name" TO current_user;
ALTER DEFAULT PRIVILEGES FOR ROLE :"scan_name" IN SCHEMA public
  GRANT SELECT ON TABLES TO :"web_name";
SQL
}

# Scan role first: it owns the schema, so create it (and set its default
# privileges) BEFORE the Jobs run create_all.
create_principal "$SCAN_ID_NAME" "$SCAN_ID_OID"
grant_scan_in_db "$SCAN_ID_NAME"

# Web role: SELECT-only, plus a one-way default SELECT on future scan-owned
# tables.
create_principal "$WEB_ID_NAME" "$WEB_ID_OID"
grant_web_in_db  "$WEB_ID_NAME" "$SCAN_ID_NAME"

echo
echo "✓ Done. Schema/data are created by the scan Jobs; scripts/deploy.sh runs"
echo "  them once before restarting the web. To refresh manually:"
echo "    az webapp restart -n $APP_NAME -g $RG_NAME"
