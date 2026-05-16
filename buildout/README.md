# Buildout — deploy the Azure RBAC Catalog from scratch

Bicep templates + helper scripts to provision the Azure infrastructure for
the Azure RBAC Catalog. Plan for **~15 minutes** end-to-end.

See [ARCHITECTURE.md](ARCHITECTURE.md) for design rationale.

> A CDN / WAF in front of the App Service is **optional**. Both Cloudflare
> and Azure Front Door are valid options — configure either externally;
> neither is provisioned by these templates.

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

**Out of scope by design** — intentionally NOT provisioned by this buildout
so the templates stay small and predictable:

| Skipped | Why |
|---|---|
| **Ollama VM / VNet** | An always-on VM-hosted Ollama is expensive (~$30 / month) and rarely useful for a public demo site. The app degrades gracefully when `OLLAMA_BASE_URL` is empty — the catalog still browses, search still works, only the LLM-backed recommendation modes are disabled. Bring your own Ollama-compatible endpoint and set `OLLAMA_BASE_URL` in `.env` if you want AI features. |
| **Automation Account / runbooks** | Useful only as housekeeping (ACR pruning, PPE-slot shutdown). ACR Basic does not support retention policies anyway; prune images out of band when needed. Removing this avoids shipping empty runbook scaffolds. |

Two profiles ship out of the box:
- [`parameters/prod.bicepparam`](parameters/prod.bicepparam) — production baseline with slots (~$77 / month)
- [`parameters/dev.bicepparam`](parameters/dev.bicepparam) — mandatory only, cheap SKUs (~$20 / month)

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
target subscription.

---

## Deploy from scratch (5 steps)

### 1. Pick your subscription

```bash
az account set --subscription <subscription_id>
```

### 2. Pick your names

Edit [`parameters/prod.bicepparam`](parameters/prod.bicepparam) — only **two
lines** matter for naming:

```bicep
param resourceGroupName = 'myapp-rg'
param baseName          = 'myapp'        // 3-15 alphanumeric chars
```

`baseName` is the prefix for every resource (`<base>-app`, `<base>registry`,
`<base>-pg`, …). The App Service, ACR, and PG server names must be
globally unique — pick something distinctive.

If you don't need slots, flip the flag in the same file:

```bicep
param deploySlots = false
```

### 3. Set secrets in `.env`

```bash
cp .env.example .env
$EDITOR .env       # fill in the placeholders
source .env
```

`.env` is gitignored. For non-interactive / CI deploys, see
[§ Other ways to handle secrets](#other-ways-to-handle-secrets).

### 4. Deploy

```bash
./scripts/deploy.sh prod        # or 'dev' for the dev profile
```

`deploy.sh`:
1. Validates the template against Azure.
2. Runs `az deployment sub create` (10-15 min — PG is the slow step).
3. Saves the deployment outputs to `.deploy-outputs.json`.
4. Configures Entra ID auth on PostgreSQL (runs
   [`grant-postgres-aad-admin.sh`](scripts/grant-postgres-aad-admin.sh)
   automatically — pass `--skip-pg-grant` to skip).

To preview changes without applying them:

```bash
./scripts/deploy.sh prod --what-if
```

### 5. Push the first image + populate the DB

The App Service is now running but stuck — no image exists yet. Build it
and restart:

```bash
ACR=$(jq -r .acrName.value .deploy-outputs.json)
APP=$(jq -r .appServiceName.value .deploy-outputs.json)
RG=$(jq -r  .resourceGroupName.value .deploy-outputs.json)
URL=$(jq -r .appServiceUrl.value .deploy-outputs.json)

# From the repo root (where Dockerfile lives)
cd ..
az acr build --registry "$ACR" --image azurerbac:latest \
  --build-arg VERSION="$(git describe --tags --always)" .
cd buildout

az webapp restart -n "$APP" -g "$RG"
```

The background worker runs the first role + operations scan automatically
on startup (the buildout sets `RUN_SCAN_ON_STARTUP=true` and
`RUN_OPERATIONS_SCAN_ON_STARTUP=true`). The catalog is populated within a
few minutes; no manual scan invocation is required.

**Verify**: `curl -fsS "$URL/healthz"` returns `{"status":"ok"}` and the
homepage at `$URL` loads.

---

## Other ways to handle secrets

`.env` is fine for one-off deploys. For automation, the `.bicepparam` files
already use `readEnvironmentVariable()`, so any mechanism that exports the
variable will work — including:

- **Interactive prompt**: `read -rs -p "Password: " PG_ADMIN_PASSWORD && export PG_ADMIN_PASSWORD`
- **Azure Key Vault references** at deploy time — Microsoft's recommended
  pattern, nothing on disk. See
  [docs](https://learn.microsoft.com/azure/azure-resource-manager/templates/key-vault-parameter).
- **GitHub Actions OIDC** — no secret stored at all; see CI section below.

---

## CI/CD with GitHub Actions

The repository already ships with workflows in
[`.github/workflows/`](../.github/workflows/) (build, deploy, deploy-ppe,
rollback). They authenticate to Azure via **OIDC federation** — no client
secret is stored in GitHub.

One-time setup, after your first successful `deploy.sh`:

```bash
./scripts/setup-github-oidc.sh <github-org-or-user>/<repo>
```

The script creates the Entra ID app, federated credentials (one per GitHub
environment used by the workflows: `staging`, `production`, `ppe`, plus
`main` branch and pull requests), and **least-privilege** role assignments:

- `AcrPush` on the registry
- `Website Contributor` on the App Service
- `Reader` on the resource group

It does **not** grant Contributor on the RG. If you later need Bicep
redeploys from CI, add a narrower custom role rather than widening
Contributor.

Then add these to **GitHub → Settings → Secrets and variables → Actions**:

| Type | Name |
|---|---|
| Secret | `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` |
| Variable | `ACR_NAME`, `APP_SERVICE_NAME`, `RESOURCE_GROUP_NAME` |

> ⚠️ The shipped workflows in [`.github/workflows/`](../.github/workflows/)
> currently hard-code `APP_NAME`, `RESOURCE_GROUP`, `ACR_NAME`, and
> `ACR_LOGIN_SERVER` in their `env:` blocks. If you deploy with custom
> names, edit those `env:` values to match (or change the workflows to
> read `${{ vars.APP_SERVICE_NAME }}` etc.) — setting the GitHub
> Variables alone is not enough until the workflows pick them up.

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

Read names from `.deploy-outputs.json` to avoid hardcoding:

```bash
APP=$(jq -r .appServiceName.value     .deploy-outputs.json)
RG=$(jq -r  .resourceGroupName.value  .deploy-outputs.json)

az webapp log tail   -n "$APP" -g "$RG"                                            # live logs
az webapp restart    -n "$APP" -g "$RG"                                            # restart
az webapp deployment slot swap -n "$APP" -g "$RG" --slot ppe --target-slot production
```

---

## Cost estimate (West Europe, list price)

| Tier | ~ Monthly |
|---|---|
| **Mandatory only** (P0v3 + B1ms PG + ACR Basic + Log Analytics) | **≈ $77** |
| + slots (free with the plan) | + $0 |
| **Full prod profile** | **≈ $77** |

Dev profile lands at **≈ $20 / month**.

---

## Troubleshooting

**`ResourceNameNotAvailable`** — the App Service / ACR / PG name is taken
globally. Pick a more distinctive `baseName`.

**Container won't start (5xx)** — no image yet. Run step 5 above.

**PG auth errors in the app logs** — `grant-postgres-aad-admin.sh` didn't
run (or failed). Re-run it manually and restart the App Service.

**`RoleAssignmentExists` on re-deploy** — safe to ignore; the AcrPull
assignment uses `guid()` and is idempotent.

**AI recommendations return only TF-IDF / embedding results** — the
buildout does NOT provision Ollama. Set `OLLAMA_BASE_URL` in `.env`
(pointing at an Ollama-compatible endpoint of your choice) and redeploy
to enable the LLM-backed modes.

---

## Folder layout

```text
buildout/
├── README.md                          # this file
├── ARCHITECTURE.md                    # diagrams + design rationale
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
    ├── render-grafana-dashboard.sh    # fills the dashboard template
    └── setup-github-oidc.sh           # creates the GitHub OIDC identity (per-environment FIDs)
```

The CI workflows live at the repo root in
[`.github/workflows/`](../.github/workflows/) — they're standard GitHub
Actions files, not specific to this folder.

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
| **Container image** | App Service pulls `azurerbac:latest` | Pin to an immutable tag or digest in CI: pass `imageTag` to `appservice.bicep` (e.g. a Git SHA). |
| **ACR public network** | `publicNetworkAccess = 'Enabled'`, `adminUserEnabled = false`, `anonymousPullEnabled = false` | For a closed network, switch to a Private Endpoint (Premium SKU required). |
| **GitHub Actions identity** | OIDC (no client secret) + scoped roles: **AcrPush** on ACR + **Website Contributor** on App + **Reader** on RG, plus per-environment federated credentials (`staging`, `production`, `ppe`) | If your workflow needs Bicep redeploy, add a narrower custom role rather than Contributor on the RG. |
| **Diagnostic logging** | All resources (App, PG, ACR) forward `allLogs` + `AllMetrics` to Log Analytics | Already on. Add Defender for Cloud / Microsoft Defender for Containers for vulnerability scanning. |
| **Backups** | PG backup retention 7 days, no geo-redundancy | Increase `backupRetentionDays` and enable `geoRedundantBackup` for production. |
| **Secrets in Bicep params** | `postgresAdminPassword` comes from an environment variable (`.env`, gitignored) — never written to disk in the repo | Already on. The `.bicepparam` files use `readEnvironmentVariable()`. |

