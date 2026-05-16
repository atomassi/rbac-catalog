#!/usr/bin/env bash
# ============================================================================
# setup-github-oidc.sh
# ============================================================================
# Creates the Entra ID application + federated credentials needed for the
# GitHub Actions workflow to authenticate to Azure without storing a client
# secret. Reads resource names from `.deploy-outputs.json`. Idempotent.
#
# Usage:
#   ./scripts/setup-github-oidc.sh <github-org-or-user>/<repo>
# ============================================================================

set -euo pipefail

REPO="${1:-}"
if [[ -z "$REPO" || "$REPO" != */* ]]; then
  echo "Usage: $0 <github-org-or-user>/<repo>" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILDOUT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$BUILDOUT_DIR"

OUTPUTS_FILE=".deploy-outputs.json"
if [[ ! -f "$OUTPUTS_FILE" ]]; then
  echo "Run scripts/deploy.sh first — $OUTPUTS_FILE not found." >&2
  exit 1
fi

command -v jq >/dev/null || { echo "jq is required"; exit 1; }

RG=$(jq -r              '.resourceGroupName.value'  "$OUTPUTS_FILE")
ACR=$(jq -r             '.acrName.value'            "$OUTPUTS_FILE")
APP_NAME_VAR=$(jq -r    '.appServiceName.value'     "$OUTPUTS_FILE")
SUBSCRIPTION_ID=$(jq -r '.subscriptionId.value'     "$OUTPUTS_FILE")
TENANT_ID=$(az account show --query tenantId -o tsv)

# Name of the AAD app registration this script creates. Includes a slug of
# the repo so multiple repos in the same tenant don't clash.
SAFE_REPO=$(echo "$REPO" | tr '/' '-' | tr '[:upper:]' '[:lower:]')
APP_REG_NAME="gh-oidc-${SAFE_REPO}"

echo "Repo                : $REPO"
echo "Resource group      : $RG"
echo "Container registry  : $ACR"
echo "App Service         : $APP_NAME_VAR"
echo "App registration    : $APP_REG_NAME"
echo

# ---------------------------------------------------------------------------
# 1. App registration + service principal
# ---------------------------------------------------------------------------

CLIENT_ID=$(az ad app list --display-name "$APP_REG_NAME" --query '[0].appId' -o tsv)
if [[ -z "$CLIENT_ID" ]]; then
  echo "→ Creating app registration $APP_REG_NAME..."
  CLIENT_ID=$(az ad app create --display-name "$APP_REG_NAME" --query appId -o tsv)
fi

SP_OBJECT_ID=$(az ad sp show --id "$CLIENT_ID" --query id -o tsv 2>/dev/null || echo "")
if [[ -z "$SP_OBJECT_ID" ]]; then
  echo "→ Creating service principal..."
  SP_OBJECT_ID=$(az ad sp create --id "$CLIENT_ID" --query id -o tsv)
fi

# ---------------------------------------------------------------------------
# 2. Role assignments — least privilege
# ---------------------------------------------------------------------------
# The CI workflow needs to:
#   - push images to ACR             → AcrPush on the ACR
#   - swap slots / restart App       → Website Contributor on the App Service
#   - read RG-level metadata         → Reader on the resource group
# We deliberately do NOT grant Contributor on the resource group. If your CI
# workflow needs to redeploy Bicep, grant a narrower custom role or run the
# deploy step from a separate, manually-triggered workflow.

APP_SERVICE_ID="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RG}/providers/Microsoft.Web/sites/${APP_NAME_VAR}"
RG_SCOPE="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RG}"
ACR_ID=$(az acr show --name "$ACR" --query id -o tsv)

assign_role() {
  local role="$1" scope="$2"
  echo "→ Granting '$role' on $scope..."
  az role assignment create \
    --assignee-object-id "$SP_OBJECT_ID" \
    --assignee-principal-type ServicePrincipal \
    --role "$role" \
    --scope "$scope" \
    --output none 2>/dev/null || true
}

assign_role "AcrPush"              "$ACR_ID"
assign_role "Website Contributor"  "$APP_SERVICE_ID"
assign_role "Reader"               "$RG_SCOPE"

# ---------------------------------------------------------------------------
# 3. Federated credentials
# ---------------------------------------------------------------------------

create_federated() {
  local name="$1" subject="$2"
  local exists
  exists=$(az ad app federated-credential list --id "$CLIENT_ID" \
             --query "length([?name=='$name'])" -o tsv)
  if [[ "$exists" != "0" ]]; then
    echo "  ✓ $name already exists"
    return
  fi
  echo "  + creating $name ($subject)"
  az ad app federated-credential create \
    --id "$CLIENT_ID" \
    --parameters "$(cat <<EOF
{
  "name": "$name",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "$subject",
  "audiences": ["api://AzureADTokenExchange"]
}
EOF
)" --output none
}

echo "→ Adding federated credentials for $REPO..."
create_federated "main-branch"       "repo:${REPO}:ref:refs/heads/main"
create_federated "pull-requests"     "repo:${REPO}:pull_request"

# ---------------------------------------------------------------------------
# 4. Print GitHub secrets / variables
# ---------------------------------------------------------------------------

cat <<EOF

────────────────────────────────────────────────────────────────────────────
 Set these in GitHub → Settings → Secrets and variables → Actions
────────────────────────────────────────────────────────────────────────────

Secrets:
  AZURE_CLIENT_ID         = $CLIENT_ID
  AZURE_TENANT_ID         = $TENANT_ID
  AZURE_SUBSCRIPTION_ID   = $SUBSCRIPTION_ID

Variables:
  ACR_NAME                = $ACR
  APP_SERVICE_NAME        = $APP_NAME_VAR
  RESOURCE_GROUP_NAME     = $RG

The workflows in .github/workflows/ will then authenticate via OIDC without
storing any client secret in GitHub.
EOF
