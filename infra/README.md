# Infra — deploy the Azure RBAC Catalog from scratch

Bicep templates + helper scripts to provision the Azure infrastructure for
the Azure RBAC Catalog. Plan for **~15 minutes** end-to-end.

---

> [!TIP]
> ### Put it behind a reverse proxy / CDN for production
>
> The infra provisions the App Service with its default
> `*.azurewebsites.net` hostname. That's fine for **dev, staging, internal
> tools, prototypes, and low-risk apps**. For an internet-facing production
> deployment, you should put **Cloudflare** or **Azure Front Door + WAF** in
> front and restrict the App Service so users can't reach it directly.
>
> You get:
>
> - **WAF** (OWASP rule set, custom rules).
> - **DDoS protection** at the edge.
> - **TLS termination & certificate management.**
> - **Bot filtering and edge rate-limiting.**
> - **Caching + global anycast** routing for latency / failover.
> - **Origin hiding** — the App Service IP / hostname is never exposed.
> - **Cleaner DNS** (use your custom domain; drop the `*.azurewebsites.net`
>   default).
>
> **References**
>
> - [Azure Front Door overview](https://learn.microsoft.com/azure/frontdoor/front-door-overview)
> - [App Service access restrictions](https://learn.microsoft.com/azure/app-service/app-service-ip-restrictions)
> - [Private endpoint for App Service](https://learn.microsoft.com/azure/app-service/networking/private-endpoint)
> - [Cloudflare — protect your origin server](https://developers.cloudflare.com/fundamentals/security/protect-your-origin-server/)

---

## What gets deployed

**Mandatory** — the app does not run without these:

- App Service Plan (Linux, P0v3) + App Service (system-assigned MI)
- Azure Container Registry (Basic)
- PostgreSQL Flexible Server v17 — Entra ID auth (password auth is also
  enabled on first deploy so the AAD-mapped role can be provisioned; see
  [§ Security notes](#security-notes))
- Log Analytics workspace + Application Insights

**Optional** (feature flag in your `.bicepparam`):

| Flag | Adds | Status |
|---|---|---|
| `deploySlots` | `staging` + `ppe` deployment slots | **Recommended** for blue/green deploys (not required) |

**Out of scope by design** — intentionally NOT provisioned by this infra
so the templates stay small and predictable:

| Skipped | Why |
|---|---|
| **Ollama VM / VNet** | An always-on VM-hosted Ollama is expensive (~$50 / month) and rarely useful for a public demo site. The app degrades gracefully when `OLLAMA_BASE_URL` is empty — the catalog still browses, search still works, only the LLM-backed recommendation modes are disabled. Bring your own Ollama-compatible endpoint and set `OLLAMA_BASE_URL` in `.env` if you want AI features. |
| **Automation Account / runbooks** | Useful only as housekeeping (ACR pruning, PPE-slot shutdown). ACR Basic does not support retention policies anyway; prune images out of band when needed. Removing this avoids shipping empty runbook scaffolds. |

Two profiles ship out of the box:
- [`parameters/prod.bicepparam`](parameters/prod.bicepparam) — production baseline with slots (~$65 / month)
- [`parameters/dev.bicepparam`](parameters/dev.bicepparam) — mandatory only, cheap SKUs (~$20 / month)

---

## Design notes

- **App Service over AKS / Container Apps** — single-container app; free TLS, slot swaps, and App Insights integration are built in.
- **P0v3 plan** — smallest Premium V3 (VNet integration, AlwaysOn, 5 free slots).
- **User-assigned MIs (writer + reader)** — two UAMIs decouple identity lifecycle from the app and split privilege. A **writer** is attached to the production slot (AcrPull + full CRUD on PostgreSQL); a shared **reader** is attached to staging + ppe (AcrPull + read-only via `pg_read_all_data`). Splitting identities means a non-production slot physically cannot write to the catalog.
- **PostgreSQL Flexible (not Single)** — better price/perf and Entra ID auth. B1ms is enough for the workload (< 5 RPS, ~200 MB data).
- **Entra ID for DB auth** — the app opens password-less, token-based connections; rotation is automatic via MSI tokens. The admin password is only needed at first deploy to provision the AAD-mapped role (see [§ Security notes](#security-notes)).
- **Slot-sticky config** — `MSI_CLIENT_ID`, `MSI_DB_USER`, `APP_ENVIRONMENT_NAME`, `SCAN_DRY_RUN`, and the scan-enable flags (`ROLE_SCAN_ENABLED`, `OPERATIONS_SCAN_ENABLED`) are registered in `slotConfigNames`, so a slot swap does NOT carry a slot's identity, PG role, telemetry label, or scanner mode with the code. Only the **production slot** writes scan results; staging and ppe run the same scans in what-if mode (`SCAN_DRY_RUN=true`) — they fetch and log changes but never write, so they can't race the worker or double-count events.

---

## Prerequisites

```bash
# Azure CLI + Bicep
az version                                   # ≥ 2.60
az bicep install                             # one-time

# jq + psql (post-deploy DB grants)
brew install jq libpq                        # macOS
# or
sudo apt-get install jq postgresql-client    # Debian/Ubuntu

az login
```

You also need **Owner** (or Contributor + User Access Administrator) on the
target subscription so the deployment can create the role assignments that
wire AcrPull / DB grants automatically.

---

## Deploy from scratch — copy/paste walkthrough

Total wall time: **~15 min**. Run every step from `infra/`.

### 1. Create your `.env`

```bash
cd infra
cp .env.example .env
$EDITOR .env                              # fill in SUBSCRIPTION_ID, RG_NAME,
                                          # BASE_NAME, LOCATION, PG_ADMIN_PASSWORD
source .env
```

> `BASE_NAME` becomes `<base>-app`, `<base>registry`, `<base>-pg`, etc.
> The App Service, ACR, and PG server names are part of public DNS, so
> pick something short and distinctive (3-15 alphanumeric).

### 2. Log in to Azure

```bash
az login
az account set --subscription "$SUBSCRIPTION_ID"
az bicep install                          # one-time, no-op if already installed
```

### 3. (Optional) Dry-run with `--what-if`

```bash
./scripts/deploy.sh prod --what-if
```

Expected output: a list of resources to **Create** under
`resourceGroups/$RG_NAME`. Any 3 "Unsupported" role-assignment entries
are normal — they reference managed-identity principalIds that only
exist after the apps are deployed.

### 4. Deploy

```bash
./scripts/deploy.sh prod
```

`deploy.sh` does, in order:
1. Validates the template against Azure (~10 s).
2. Runs `az deployment sub create` (~10–15 min — PG is the slow step).
3. Saves outputs to `infra/.deploy-outputs.json`.
4. Configures Entra ID auth on PostgreSQL by calling
   [`grant-postgres-aad-admin.sh`](scripts/grant-postgres-aad-admin.sh)
   (registers the App Service MI + each slot MI as PG roles and grants
   them CRUD on the `azurerbac` database). Pass `--skip-pg-grant` to
   skip this step.

### 5. Push the first image

The App Service is now running but has no image to pull. Build and push
it from the repo root:

```bash
cd ..                                     # back to repo root (Dockerfile lives here)
OUT=infra/.deploy-outputs.json
ACR=$(jq -r .acrName.value         "$OUT")
APP=$(jq -r .appServiceName.value  "$OUT")
RG=$(jq  -r .resourceGroupName.value "$OUT")
URL=$(jq -r .appServiceUrl.value   "$OUT")

az acr build --registry "$ACR" --image rbaccatalog:latest \
    --build-arg VERSION="$(git describe --tags --always 2>/dev/null || echo dev)" .

az webapp restart -n "$APP" -g "$RG"
```

The background worker (production slot only — staging/ppe are
intentionally non-writers) runs the first role + operations scan on
startup. The catalog populates within a few minutes.

### 6. Verify

```bash
# Health endpoint
curl -fsS "$URL/healthz"
# {"status":"ok"}

# Homepage
curl -fsS -o /dev/null -w "%{http_code}\n" "$URL"
# 200
```

Open `$URL` in a browser and you should see the role catalog.

---

## Other ways to handle secrets

`.env` is fine for one-off deploys. For automation, the `.bicepparam` files
already use `readEnvironmentVariable()`, so any mechanism that exports the
variable will work — including:

- **Interactive prompt**: `read -rs -p "Password: " PG_ADMIN_PASSWORD && export PG_ADMIN_PASSWORD`
- **Azure Key Vault references** at deploy time — Microsoft's recommended
  pattern, nothing on disk. See
  [docs](https://learn.microsoft.com/azure/azure-resource-manager/templates/key-vault-parameter).
- **CI variables** — export `PG_ADMIN_PASSWORD`, `SUBSCRIPTION_ID`, `RG_NAME`,
  `BASE_NAME`, `LOCATION` from your CI system's secret store before invoking
  `./scripts/deploy.sh`.

---

## Continuous deployment

The infra stops at "infrastructure ready". Wiring up continuous deployment
to the App Service is intentionally out of scope so you can use whichever
pipeline you prefer:

- **GitHub Actions** — see Azure's
  [login-via-OIDC guide](https://learn.microsoft.com/azure/developer/github/connect-from-azure)
  and the
  [`azure/webapps-deploy`](https://github.com/Azure/webapps-deploy) action.
- **Azure DevOps** — use the
  [Azure Web App for Containers](https://learn.microsoft.com/azure/devops/pipelines/tasks/reference/azure-web-app-container-v1)
  task with a workload-identity service connection.
- **Direct from the Portal** — App Service → *Deployment Center* → choose
  *Container Registry* (recommended for this image), *GitHub Actions*, or
  *Azure Pipelines*. The Portal will configure credentials and write the
  required app settings for you.

Whichever option you pick, the App Service is already configured to pull
its image with the system-assigned managed identity (`AcrPull` granted on
the registry by Bicep), so your pipeline only needs *push* permission on
the ACR — no admin credentials required.

---

## Grafana dashboard

A portable, 50-panel dashboard for the Application Insights telemetry
ships as a JSON template
([`dashboards/grafana-appinsights.template.json`](dashboards/grafana-appinsights.template.json))
with three placeholders for subscription / resource group / App Insights
name.

```bash
./scripts/render-grafana-dashboard.sh -o /tmp/dashboard.json
```

The script reads the placeholders from `.deploy-outputs.json`. In Grafana →
**Dashboards → Import → Upload JSON file**, pick the rendered file, and
select your Azure Monitor data source. Works with both **Azure Managed
Grafana** (use managed identity with `Monitoring Reader` on the RG) and
self-hosted Grafana (install the `grafana-azure-monitor-datasource`
plugin).

---

## Common commands

Read names from `.deploy-outputs.json` to avoid hardcoding (run from
`infra/`):

```bash
APP=$(jq -r .appServiceName.value    .deploy-outputs.json)
RG=$(jq  -r .resourceGroupName.value .deploy-outputs.json)

az webapp log tail   -n "$APP" -g "$RG"                                            # live logs
az webapp restart    -n "$APP" -g "$RG"                                            # restart
az webapp deployment slot swap -n "$APP" -g "$RG" --slot ppe --target-slot production
```

---

## Cost estimate (West Europe)

| Profile | ~ Monthly |
|---|---|
| **Prod** (P0v3 + B1ms PG + ACR Basic + Log Analytics, slots included free) | **≈ $65** |
| **Dev** (mandatory only, cheap SKUs) | **≈ $20** |

Slots add no cost — they share the App Service Plan. The prod figure assumes
light Log Analytics ingestion.

---

## Troubleshooting

**`ResourceNameNotAvailable`** — the App Service / ACR / PG name is taken
globally. Pick a more distinctive `baseName`.

**Container won't start (5xx)** — no image yet. Run step 5 of the
walkthrough.

**PG auth errors in the app logs** — `grant-postgres-aad-admin.sh` didn't
run (or failed). Re-run it manually and restart the App Service.

**`RoleAssignmentExists` on re-deploy** — safe to ignore; the AcrPull
assignment uses `guid()` and is idempotent.

**AI recommendations return only TF-IDF / embedding results** — the
infra does NOT provision Ollama. Set `OLLAMA_BASE_URL` in `.env`
(pointing at an Ollama-compatible endpoint of your choice) and redeploy
to enable the LLM-backed modes.

---

## Folder layout

```text
infra/
├── README.md                          # this file
├── .env.example  /  .gitignore
├── bicepconfig.json                   # strict linter rules
├── main.bicep                         # single subscription-scoped template
├── parameters/
│   ├── prod.bicepparam                # slots ON
│   └── dev.bicepparam                 # mandatory only
├── modules/
│   ├── monitoring.bicep               # Log Analytics + App Insights
│   ├── acr.bicep                      # Container Registry + AcrPull (incl. slots)
│   ├── postgres.bicep                 # PG Flexible Server + AAD auth
│   └── appservice.bicep               # Plan + app + optional staging/ppe slots
├── dashboards/
│   └── grafana-appinsights.template.json
└── scripts/
    ├── deploy.sh                      # validate + deploy + DB grant
    ├── grant-postgres-aad-admin.sh    # PG Entra ID auth + grants (incl. slot identities)
    └── render-grafana-dashboard.sh    # fills the dashboard template
```

---

## Security notes

The defaults below are convenient for a quick demo deployment. For a
production-grade deployment you should review and (for the items marked
"hardening") flip the indicated parameters.

| Surface | Default | Hardening |
|---|---|---|
| **App Service inbound** | `httpsOnly = true`, `minTlsVersion = '1.2'` | If you front the app with Cloudflare in **Flexible** mode (HTTP-to-origin), set `appServiceHttpsOnly = false`. Prefer Cloudflare **Full (strict)** + `httpsOnly = true`. |
| **PostgreSQL firewall** | `postgresAllowAllAzureServices = true` (rule `0.0.0.0` — reachable from every Azure tenant) | For production: set `postgresAllowAllAzureServices = false` and migrate to a Private Endpoint or a VNet-integrated server. The current rule is a documented Azure convenience that exposes the server to all Azure subscriptions. |
| **PostgreSQL password auth** | `postgresEnablePasswordAuth = true` on first deploy | Required so `grant-postgres-aad-admin.sh` can provision the AAD-mapped role. The app itself never uses password auth (it uses Entra ID via `USE_MANAGED_IDENTITY=true`), but the server still accepts password auth until you redeploy with `postgresEnablePasswordAuth = false`. Doing so removes the password attack surface entirely. |
| **PG admin firewall during bootstrap** | `grant-postgres-aad-admin.sh` opens the firewall to your public IP | The script uses a stable rule name (`deploy-shell-temp`) and removes the rule on exit via `trap`. |
| **Container image** | App Service pulls `rbaccatalog:latest` | Pin to an immutable tag or digest in CI: pass `imageTag` to `appservice.bicep` (e.g. a Git SHA). |
| **ACR public network** | `publicNetworkAccess = 'Enabled'`, `adminUserEnabled = false`, `anonymousPullEnabled = false` | For a closed network, switch to a Private Endpoint (Premium SKU required). |
| **Diagnostic logging** | All resources (App, PG, ACR) forward `allLogs` + `AllMetrics` to Log Analytics | Already on. Add Defender for Cloud / Microsoft Defender for Containers for vulnerability scanning. |
| **Backups** | PG backup retention 7 days, no geo-redundancy | Increase `backupRetentionDays` and enable `geoRedundantBackup` for production. |
| **Secrets in Bicep params** | `postgresAdminPassword` comes from an environment variable (`.env`, gitignored) — never written to disk in the repo | Already on. The `.bicepparam` files use `readEnvironmentVariable()`. |

