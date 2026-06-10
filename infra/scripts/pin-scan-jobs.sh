#!/usr/bin/env bash
# pin-scan-jobs.sh — re-pin this env's scan Jobs to an image after a slot swap
# (slots don't cover Container Apps Jobs). Shared by deploy.yml / rollback.yml.
# Usage: pin-scan-jobs.sh <resourceGroup> <containerAppEnv> <image>

set -euo pipefail

RESOURCE_GROUP="${1:-}"
CONTAINERAPP_ENV="${2:-}"
IMAGE="${3:-}"

if [[ -z "$RESOURCE_GROUP" || -z "$CONTAINERAPP_ENV" || -z "$IMAGE" ]]; then
  echo "Usage: $0 <resourceGroup> <containerAppEnv> <image>" >&2
  exit 1
fi

# Resolve the env's resource ID so we only touch *this* env's Jobs (safe when
# several envs share the RG). The env is standing infra, so a missing one means
# a bad name — fail loudly rather than leave Jobs on a stale image.
ENV_ID=$(az containerapp env show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINERAPP_ENV" \
  --query id -o tsv 2>/dev/null) || ENV_ID=""

if [[ -z "$ENV_ID" ]]; then
  echo "❌ Container Apps env '$CONTAINERAPP_ENV' not found in $RESOURCE_GROUP — check the name / that infra is provisioned" >&2
  exit 1
fi

JOBS=$(az containerapp job list \
  --resource-group "$RESOURCE_GROUP" \
  --query "[?properties.environmentId=='$ENV_ID' && (ends_with(name, '-role-scan') || ends_with(name, '-operations-scan'))].name" -o tsv)

if [[ -z "$JOBS" ]]; then
  echo "❌ No scan Jobs (*-role-scan / *-operations-scan) in env $CONTAINERAPP_ENV — check that infra is provisioned" >&2
  exit 1
fi

for JOB in $JOBS; do
  az containerapp job update \
    --resource-group "$RESOURCE_GROUP" \
    --name "$JOB" \
    --image "$IMAGE" \
    --output none
  echo "📌 Pinned $JOB → $IMAGE"
done
