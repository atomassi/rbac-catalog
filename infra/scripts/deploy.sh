#!/usr/bin/env bash
# ============================================================================
# deploy.sh — one-shot Azure deployment
# ============================================================================
# 1. Loads .env (secrets reach the bicepparam via readEnvironmentVariable).
# 2. Validates + runs `az deployment sub create`.
# 3. Saves outputs to .deploy-outputs.json.
# 4. Configures Entra ID auth on PostgreSQL + bootstraps the scan Jobs.
#
# Usage:
#   ./scripts/deploy.sh prod                                # default
#   ./scripts/deploy.sh dev                                 # dev profile
#   ./scripts/deploy.sh prod --what-if                      # dry run
#   ./scripts/deploy.sh prod baseName=myapp                 # ad-hoc Bicep
#                                                           # parameter override
#                                                           # (also use env vars)
# ============================================================================

set -euo pipefail

ENVIRONMENT="prod"
WHAT_IF=0
OVERRIDES=()

for arg in "$@"; do
  case "$arg" in
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
INFRA_DIR="$(dirname "$SCRIPT_DIR")"
cd "$INFRA_DIR"

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

# A scan Job (Container Apps Job) verifies it can pull its image the moment it
# is created, so the image must already exist in the ACR. The ACR, however, is
# created by this very template. We therefore deploy in two passes:
#   Pass 1 — deployScanJobs=false: create the ACR (+ everything else), no Jobs.
#   (push image)
#   Pass 2 — deployScanJobs=true:  create the Jobs against the now-present image.
# Pass 2 is incremental and fast (ARM skips the unchanged resources).
deploy_pass () {  # $1 = name suffix, $2 = deployScanJobs value
  az deployment sub create \
    --name "${DEPLOY_NAME}-$1" \
    --location "$LOCATION" \
    --template-file main.bicep \
    --parameters "$PARAM_FILE" \
    ${OVERRIDES[@]:+--parameters "${OVERRIDES[@]}"} \
    --parameters "deployScanJobs=$2" \
    --output none
}

echo "→ Pass 1/2: core infra — App Service, ACR, PostgreSQL (~10-15 min)..."
deploy_pass infra false

ACR_NAME=$(az deployment sub show --name "${DEPLOY_NAME}-infra" \
  --query 'properties.outputs.acrName.value' -o tsv)

echo "→ Building & pushing image (scan Jobs need it before they're created)..."
"$SCRIPT_DIR/build-and-push-image.sh" "$ACR_NAME"

echo "→ Pass 2/2: scan Jobs (image now present, incremental)..."
deploy_pass jobs true

FINAL_DEPLOY_NAME="${DEPLOY_NAME}-jobs"

# ---------------------------------------------------------------------------
# Save outputs
# ---------------------------------------------------------------------------

OUTPUTS_FILE=".deploy-outputs.json"
az deployment sub show --name "$FINAL_DEPLOY_NAME" \
  --query 'properties.outputs' -o json > "$OUTPUTS_FILE"

# ---------------------------------------------------------------------------
# Post-deploy: PostgreSQL Entra ID grant
# ---------------------------------------------------------------------------
# Run BEFORE the App Service restart so both PG roles exist. The scan role's
# default privileges must be in place before the scan Jobs create the schema.

echo
echo "→ Configuring PostgreSQL Entra ID auth..."
"$SCRIPT_DIR/grant-postgres-aad-admin.sh"

# The web role is SELECT-only and cannot create the schema; the scan Jobs do.
# Run both Jobs once and wait so the schema + data exist before the web first
# boots (its preload_cache refuses to start on an empty/missing schema).
RG=$(jq -r .resourceGroupName.value "$OUTPUTS_FILE")
BOOTSTRAP_OK=true
for job_out in roleScanJobName operationsScanJobName; do
  JOB=$(jq -r ".${job_out}.value // empty" "$OUTPUTS_FILE")
  if [[ -z "$JOB" ]]; then
    echo "  ✗ ${job_out} missing from deploy outputs — scan Job not deployed" >&2
    BOOTSTRAP_OK=false
    continue
  fi
  echo "→ Bootstrapping: starting $JOB..."
  EXEC=$(az containerapp job start -g "$RG" -n "$JOB" --query name -o tsv) || EXEC=""
  if [[ -z "$EXEC" ]]; then
    echo "  ✗ could not start $JOB (check the job/permissions)" >&2
    BOOTSTRAP_OK=false
    continue
  fi
  echo "  execution $EXEC — waiting (up to ~10 min)..."
  JOB_OK=false
  for _ in $(seq 1 60); do  # 60 × 10s ≈ the 600s job timeout
    STATUS=$(az containerapp job execution show -g "$RG" -n "$JOB" \
      --job-execution-name "$EXEC" --query properties.status -o tsv 2>/dev/null || echo '')
    case "$STATUS" in
      Succeeded)       echo "  ✓ $JOB succeeded"; JOB_OK=true; break ;;
      Failed|Degraded) echo "  ✗ $JOB $STATUS (check job logs)" >&2; break ;;
      *)               printf '.'; sleep 10 ;;
    esac
  done
  echo
  [[ "$JOB_OK" == true ]] || BOOTSTRAP_OK=false
done

# The web tier is SELECT-only and can't create the schema; if the scan Jobs
# didn't populate it, restarting the web just yields a site that 503s on an
# empty/missing schema. Fail loudly instead of pretending the deploy worked.
if [[ "$BOOTSTRAP_OK" != true ]]; then
  echo "✗ Bootstrap did not complete — scan Job(s) failed or timed out." >&2
  echo "  Skipping the web restart; fix the scan Jobs and re-run." >&2
  exit 1
fi

# The App Service was created in pass 1 before the image existed, so restart it
# now to pull the image. The schema + data already exist (bootstrap above), so
# the read-only web boots cleanly.
echo "→ Restarting App Service to pull the image and load the cache..."
az webapp restart \
  -n "$(jq -r .appServiceName.value "$OUTPUTS_FILE")" \
  -g "$(jq -r .resourceGroupName.value "$OUTPUTS_FILE")" \
  --output none || echo "  (restart skipped — restart it manually if the site 503s)"

# Wait for the web tier to come up. A cold start rebuilds the full in-memory
# cache (hundreds of seconds), so poll /healthz generously before giving up.
APP_URL=$(jq -r .appServiceUrl.value "$OUTPUTS_FILE")
echo "→ Waiting for $APP_URL/healthz (up to ~10 min)..."
HEALTHY=false
for _ in $(seq 1 60); do
  if curl -fsS --max-time 10 "$APP_URL/healthz" >/dev/null 2>&1; then
    HEALTHY=true
    echo "  ✓ site is healthy"
    break
  fi
  printf '.'; sleep 10
done
[[ "$HEALTHY" == true ]] || echo "  ✗ /healthz not ready yet — check the App Service logs" >&2

echo
echo "✓ Deployment complete — outputs saved to $OUTPUTS_FILE"

# ---------------------------------------------------------------------------
# Next steps
# ---------------------------------------------------------------------------

cat <<EOF

────────────────────────────────────────────────────────────────────────────
Done. The image was built and pushed, the App Service was restarted, and its
health endpoint was polled above.

Open the site:

  $APP_URL

To ship a new build later, just re-run this script — it rebuilds & repushes the
image and redeploys incrementally.

See infra/README.md for the full walkthrough.
EOF
