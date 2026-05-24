#!/usr/bin/env bash
# Render dashboards/grafana-appinsights.template.json by substituting the
# subscription / resource group / App Insights name placeholders.
# Reads .deploy-outputs.json by default; override via env vars.
#
# Usage:
#   ./scripts/render-grafana-dashboard.sh                     # writes to stdout
#   ./scripts/render-grafana-dashboard.sh -o file.json        # writes to file
#   ./scripts/render-grafana-dashboard.sh --help

set -euo pipefail

cd "$(dirname "$(dirname "$(realpath "$0")")")"

OUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o|--output) OUT="$2"; shift 2 ;;
    -h|--help)   sed -n '2,9p' "$0"; exit 0 ;;
    *)           echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

OUTPUTS=".deploy-outputs.json"
SUB="${SUBSCRIPTION_ID:-$(jq -r '.subscriptionId.value    // empty' "$OUTPUTS" 2>/dev/null || true)}"
RG="${RESOURCE_GROUP:-$(jq    -r '.resourceGroupName.value // empty' "$OUTPUTS" 2>/dev/null || true)}"
AI="${APP_INSIGHTS_NAME:-$(jq -r '.appInsightsName.value   // empty' "$OUTPUTS" 2>/dev/null || true)}"
# Public site URL shown by the dashboard's "Live Site" link. Defaults to the
# App Service URL; override with PUBLIC_SITE_URL=https://your-domain to point
# at a custom hostname behind a reverse proxy / CDN.
SITE="${PUBLIC_SITE_URL:-$(jq -r '.appServiceUrl.value     // empty' "$OUTPUTS" 2>/dev/null || true)}"

if [[ -z "$SUB" || -z "$RG" || -z "$AI" ]]; then
  echo "Missing values. Run deploy.sh first, or set SUBSCRIPTION_ID / RESOURCE_GROUP / APP_INSIGHTS_NAME." >&2
  exit 1
fi

render() {
  sed -e "s|__SUBSCRIPTION_ID__|$SUB|g" \
      -e "s|__RESOURCE_GROUP__|$RG|g" \
      -e "s|__APP_INSIGHTS_NAME__|$AI|g" \
      -e "s|__PUBLIC_SITE_URL__|$SITE|g" \
      dashboards/grafana-appinsights.template.json
}

if [[ -n "$OUT" ]]; then
  render > "$OUT"
  echo "✓ wrote $OUT" >&2
else
  render
fi
