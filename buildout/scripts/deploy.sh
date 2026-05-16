#!/usr/bin/env bash
# ============================================================================
# deploy.sh — one-shot Azure deployment
# ============================================================================
# 1. Loads .env (secrets reach the bicepparam via readEnvironmentVariable).
# 2. Validates + runs `az deployment sub create`.
# 3. Saves outputs to .deploy-outputs.json.
# 4. Configures Entra ID auth on PostgreSQL (unless --skip-pg-grant).
#
# Usage:
#   ./scripts/deploy.sh prod                                # default
#   ./scripts/deploy.sh dev                                 # dev profile
#   ./scripts/deploy.sh prod --what-if                      # dry run
#   ./scripts/deploy.sh prod --skip-pg-grant                # skip PG bootstrap
#   ./scripts/deploy.sh prod baseName=myapp                 # ad-hoc Bicep
#                                                           # parameter override
#                                                           # (also use env vars)
# ============================================================================

set -euo pipefail

ENVIRONMENT="prod"
SKIP_PG_GRANT=0
WHAT_IF=0
OVERRIDES=()

for arg in "$@"; do
  case "$arg" in
    --skip-pg-grant) SKIP_PG_GRANT=1 ;;
    --what-if)       WHAT_IF=1 ;;
    prod|dev)        ENVIRONMENT="$arg" ;;
    # Anything of the form ``name=value`` becomes an ad-hoc Bicep parameter
    # override on the az CLI call, without editing the .bicepparam file.
    # Useful for one-off deploys where you don't want to commit your name
    # choices into the repo.
    *=*)             OVERRIDES+=("$arg") ;;
    *) echo "Unknown argument: $arg" >&2; exit 1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILDOUT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$BUILDOUT_DIR"

PARAM_FILE="parameters/${ENVIRONMENT}.bicepparam"
[[ -f "$PARAM_FILE" ]] || { echo "Parameter file not found: $PARAM_FILE" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Load env vars (consumed by readEnvironmentVariable() in the .bicepparam)
# ---------------------------------------------------------------------------

if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  source .env
fi

: "${PG_ADMIN_PASSWORD:?PG_ADMIN_PASSWORD is required — see .env.example}"

# Bicepparam files read these via ``readEnvironmentVariable``; export them
# explicitly so they survive into the ``az`` subprocess.
export PG_ADMIN_PASSWORD
export RG_NAME="${RG_NAME:-myapp-rg}"
export BASE_NAME="${BASE_NAME:-myapp}"
export LOCATION="${LOCATION:-westeurope}"
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-}"

if [[ -n "${SUBSCRIPTION_ID:-}" ]]; then
  az account set --subscription "$SUBSCRIPTION_ID"
fi

DEPLOY_NAME="${ENVIRONMENT}-$(date +%Y%m%d-%H%M%S)"

echo "Deploying:  $DEPLOY_NAME"
echo "Location:   $LOCATION"
echo "RG:         $RG_NAME"
echo "Base name:  $BASE_NAME"
echo "Params:     $PARAM_FILE"
if [[ ${#OVERRIDES[@]} -gt 0 ]]; then
  echo "Overrides:  ${OVERRIDES[*]}"
fi

# ---------------------------------------------------------------------------
# Validate + deploy
# ---------------------------------------------------------------------------

echo
echo "→ Validating..."
az deployment sub validate \
  --location "$LOCATION" \
  --template-file main.bicep \
  --parameters "$PARAM_FILE" \
  ${OVERRIDES[@]:+--parameters "${OVERRIDES[@]}"} \
  --output none

if [[ "$WHAT_IF" -eq 1 ]]; then
  echo "→ Running What-If preview (no changes applied)..."
  az deployment sub what-if \
    --location "$LOCATION" \
    --template-file main.bicep \
    --parameters "$PARAM_FILE" \
    ${OVERRIDES[@]:+--parameters "${OVERRIDES[@]}"} \
    --result-format ResourceIdOnly
  echo
  echo "✓ What-If complete — no resources were created or modified."
  exit 0
fi

echo "→ Deploying (~10-15 min)..."
az deployment sub create \
  --name "$DEPLOY_NAME" \
  --location "$LOCATION" \
  --template-file main.bicep \
  --parameters "$PARAM_FILE" \
  ${OVERRIDES[@]:+--parameters "${OVERRIDES[@]}"} \
  --output none

# ---------------------------------------------------------------------------
# Save outputs
# ---------------------------------------------------------------------------

OUTPUTS_FILE=".deploy-outputs.json"
az deployment sub show --name "$DEPLOY_NAME" \
  --query 'properties.outputs' -o json > "$OUTPUTS_FILE"

echo
echo "✓ Deployment complete — outputs saved to $OUTPUTS_FILE"

# ---------------------------------------------------------------------------
# Post-deploy: PostgreSQL Entra ID grant
# ---------------------------------------------------------------------------

if [[ "$SKIP_PG_GRANT" -eq 1 ]]; then
  echo "  (skipping PG AAD grant — pass without --skip-pg-grant to run it)"
else
  echo
  echo "→ Configuring PostgreSQL Entra ID auth..."
  "$SCRIPT_DIR/grant-postgres-aad-admin.sh"
fi

# ---------------------------------------------------------------------------
# Next steps
# ---------------------------------------------------------------------------

cat <<EOF

────────────────────────────────────────────────────────────────────────────
Next steps (run from the repo root):

  cd ..
  OUT=buildout/$OUTPUTS_FILE

  # 1. Build & push the container image:
  az acr build \\
    --registry \$(jq -r .acrName.value \$OUT) \\
    --image azurerbac:latest \\
    --build-arg VERSION=\$(git describe --tags --always 2>/dev/null || echo dev) .

  # 2. Restart the App Service so it picks up the new image:
  az webapp restart \\
    -n \$(jq -r .appServiceName.value \$OUT) \\
    -g \$(jq -r .resourceGroupName.value \$OUT)

  # 3. Verify:  curl -fsS "\$(jq -r .appServiceUrl.value \$OUT)/healthz"

See buildout/README.md "Step 5" for the full walkthrough.
EOF
