#!/usr/bin/env bash
# ============================================================================
# build-and-push-image.sh — build the app container image and push it to ACR
# ============================================================================
# Single-responsibility helper, callable two ways:
#   • by deploy.sh, between its two deployment passes (a scan Job can only be
#     created once the image it pulls already exists in the ACR);
#   • standalone, to ship a new build to an already-deployed environment
#     (follow with `az webapp restart` to roll it out to the web tier).
#
# Builds remotely with `az acr build` (ACR Tasks) — no local Docker required.
#
# Usage:
#   ./scripts/build-and-push-image.sh                  # ACR from .deploy-outputs.json
#   ./scripts/build-and-push-image.sh <acrName>        # explicit registry
#   ./scripts/build-and-push-image.sh <acrName> <tag>  # explicit tag (default: latest)
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$INFRA_DIR")"

ACR_NAME="${1:-}"
IMAGE_TAG="${2:-latest}"

# Fall back to the registry recorded by the last deploy.
if [[ -z "$ACR_NAME" ]]; then
  OUTPUTS_FILE="$INFRA_DIR/.deploy-outputs.json"
  if [[ ! -f "$OUTPUTS_FILE" ]]; then
    echo "No ACR name given and $OUTPUTS_FILE not found." >&2
    echo "Usage: $0 <acrName> [imageTag]" >&2
    exit 1
  fi
  ACR_NAME="$(jq -r .acrName.value "$OUTPUTS_FILE")"
fi

VERSION="$(cd "$REPO_ROOT" && git describe --tags --always 2>/dev/null || echo dev)"

echo "→ Building & pushing ${ACR_NAME}.azurecr.io/rbaccatalog:${IMAGE_TAG} (VERSION=$VERSION)..."
az acr build \
  --registry "$ACR_NAME" \
  --image "rbaccatalog:${IMAGE_TAG}" \
  --build-arg VERSION="$VERSION" \
  "$REPO_ROOT"

echo "✓ Image pushed: ${ACR_NAME}.azurecr.io/rbaccatalog:${IMAGE_TAG}"
