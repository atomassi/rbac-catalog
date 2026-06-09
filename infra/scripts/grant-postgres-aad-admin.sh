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

RG_NAME=$(jq -r            '.resourceGroupName.value'  "$OUTPUTS_FILE")
APP_NAME=$(jq -r           '.appServiceName.value'     "$OUTPUTS_FILE")
# Writer = production slot (full CRUD). Reader = staging/ppe slots (SELECT
# only). The reader values are emitted only when ``deploySlots = true``;
# treat missing/empty as "no slots" so a slotless deployment Just Works.
WRITER_OID=$(jq -r         '.writerPrincipalId.value'  "$OUTPUTS_FILE")
WRITER_ROLE=$(jq -r        '.writerDbRole.value'       "$OUTPUTS_FILE")
READER_OID=$(jq -r         '.readerPrincipalId.value // empty' "$OUTPUTS_FILE")
READER_ROLE=$(jq -r        '.readerDbRole.value     // empty' "$OUTPUTS_FILE")
PG_FQDN=$(jq -r            '.postgresFqdn.value'       "$OUTPUTS_FILE")
PG_SERVER="${PG_FQDN%%.*}"
PG_DB="azurerbac"

CURRENT_UPN=$(az ad signed-in-user show --query userPrincipalName -o tsv)
CURRENT_OID=$(az ad signed-in-user show --query id -o tsv)

echo "Resource group  : $RG_NAME"
echo "PG server       : $PG_SERVER"
echo "Database        : $PG_DB"
echo "Writer MI       : $WRITER_ROLE (oid=$WRITER_OID)"
[[ -n "$READER_OID" ]] && echo "Reader MI       : $READER_ROLE (oid=$READER_OID)"
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
# Two identities, split by privilege:
#   * writer (production slot) — full CRUD; the single catalog writer.
#   * reader (staging/ppe slots) — SELECT only, so a non-production slot
#     physically cannot write to (or race) the catalog.
# Each gets its own pgaadauth principal; the reader is granted a strictly
# read-only set of privileges.

echo "→ Granting CONNECT/USAGE + privileges on $PG_DB to each MI..."

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

grant_in_db() {
  local mi_name="$1"
  echo "  • grant on $PG_DB: $mi_name"
  PGPASSWORD="$PG_TOKEN" psql \
    "host=$PG_FQDN port=5432 dbname=$PG_DB user=$CURRENT_UPN sslmode=require" \
    -v ON_ERROR_STOP=1 \
    -v APP_MI_NAME="$mi_name" \
    <<'SQL'
\set role_name :APP_MI_NAME

GRANT CONNECT ON DATABASE azurerbac TO :"role_name";

-- USAGE lets the role resolve names in ``public``; CREATE lets it own the
-- schema (the app's SQLAlchemy ``Base.metadata.create_all`` runs at first
-- start and is the source of truth for the schema).
GRANT USAGE, CREATE ON SCHEMA public TO :"role_name";

GRANT SELECT, INSERT, UPDATE, DELETE
  ON ALL TABLES    IN SCHEMA public TO :"role_name";

GRANT USAGE, SELECT
  ON ALL SEQUENCES IN SCHEMA public TO :"role_name";

-- Future tables the app creates on first start.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES    TO :"role_name";
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT                  ON SEQUENCES TO :"role_name";
SQL
}

grant_identity() {
  local mi_name="$1" mi_oid="$2"
  create_principal "$mi_name" "$mi_oid"
  grant_in_db      "$mi_name"
}

# Read-only counterpart of ``grant_in_db``: CONNECT + ``pg_read_all_data``.
# No CREATE/INSERT/UPDATE/DELETE — the reader (staging/ppe) must never be
# able to mutate the catalog. ``pg_read_all_data`` is used instead of explicit
# SELECT + ALTER DEFAULT PRIVILEGES because the writer (a different role) owns
# the tables: default privileges only apply to objects created by the role
# that sets them, so they would NOT cover writer-created tables. The built-in
# ``pg_read_all_data`` role grants SELECT on every current and future object.
grant_in_db_readonly() {
  local mi_name="$1"
  echo "  • grant (read-only) on $PG_DB: $mi_name"
  PGPASSWORD="$PG_TOKEN" psql \
    "host=$PG_FQDN port=5432 dbname=$PG_DB user=$CURRENT_UPN sslmode=require" \
    -v ON_ERROR_STOP=1 \
    -v APP_MI_NAME="$mi_name" \
    <<'SQL'
\set role_name :APP_MI_NAME

GRANT CONNECT ON DATABASE azurerbac TO :"role_name";

-- ``pg_read_all_data`` (PG 14+) confers SELECT on every table/view/sequence
-- and USAGE on every schema, INCLUDING objects the writer creates later, with
-- no write or DDL rights. This sidesteps ALTER DEFAULT PRIVILEGES, which would
-- only cover tables created by the admin running this script — not the writer.
GRANT pg_read_all_data TO :"role_name";
SQL
}

grant_identity_readonly() {
  local mi_name="$1" mi_oid="$2"
  create_principal      "$mi_name" "$mi_oid"
  grant_in_db_readonly  "$mi_name"
}

# Writer (production slot) — full CRUD. The production slot's bicep
# ``MSI_DB_USER`` setting uses exactly this role name.
grant_identity "$WRITER_ROLE" "$WRITER_OID"

# Reader (staging + ppe slots) — SELECT only. Both non-production slots
# share this single identity/role, so one read-only grant covers both. The
# slots' bicep ``MSI_DB_USER`` setting points at this role name.
if [[ -n "$READER_OID" ]]; then
  grant_identity_readonly "$READER_ROLE" "$READER_OID"
fi

echo
echo "✓ Done. Restart the App Service to refresh its DB connection pool:"
echo "    az webapp restart -n $APP_NAME -g $RG_NAME"
