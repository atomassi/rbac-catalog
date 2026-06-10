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

ENV_ID=$(az containerapp env show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINERAPP_ENV" \
  --query id -o tsv)

JOBS=$(az containerapp job list \
  --resource-group "$RESOURCE_GROUP" \
  --query "[?properties.environmentId=='$ENV_ID' && (ends_with(name, '-role-scan') || ends_with(name, '-operations-scan'))].name" -o tsv)

if [[ -z "$JOBS" ]]; then
  echo "⚠️ No scan Jobs (*-role-scan / *-operations-scan) found in env $CONTAINERAPP_ENV — skipping pin"
  exit 0
fi

for JOB in $JOBS; do
  az containerapp job update \
    --resource-group "$RESOURCE_GROUP" \
    --name "$JOB" \
    --image "$IMAGE" \
    --output none
  echo "📌 Pinned $JOB → $IMAGE"
done
