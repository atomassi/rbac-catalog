# Grafana dashboard (Application Insights data source)

`grafana-appinsights.template.json` is a Grafana / Azure Managed Grafana dashboard
that visualizes the app's telemetry from Application Insights — request volume and
latency, scan results, and the custom metrics/dimensions defined in
[`rbaccatalog/telemetry/`](../../rbaccatalog/telemetry).

The JSON is a **template**: it contains `__SUBSCRIPTION_ID__`, `__RESOURCE_GROUP__`,
`__APP_INSIGHTS_NAME__`, and `__PUBLIC_SITE_URL__` placeholders. Render a concrete
copy with the helper script, then import it into Grafana.

## Populate / render

```bash
# Reads infra/.deploy-outputs.json (written by deploy.sh) by default:
infra/scripts/render-grafana-dashboard.sh -o dashboard.json

# Or supply the values explicitly:
SUBSCRIPTION_ID=... RESOURCE_GROUP=... APP_INSIGHTS_NAME=... \
  infra/scripts/render-grafana-dashboard.sh -o dashboard.json
```

Then import `dashboard.json` in Grafana via **Dashboards → New → Import**.

> The metric and dimension names are an external contract with
> [`metric_names.py`](../../rbaccatalog/telemetry/metric_names.py) and
> [`dimensions.py`](../../rbaccatalog/telemetry/dimensions.py) — keep them in sync.
