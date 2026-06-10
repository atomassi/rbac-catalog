# Infra — deploy the Azure RBAC Catalog from scratch

Bicep templates + helper scripts to provision the Azure infrastructure for
the Azure RBAC Catalog. Plan for **~45–60 minutes** end-to-end.

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

- App Service Plan (Linux, P0v3) + App Service (read-only web user-assigned MI) — web tier
- Container Apps Environment (Consumption) + two cron Jobs (`role-scan`,
  `operations-scan`) sharing the read-write scan user-assigned MI — the
  background scans, decoupled from the web image (scale-to-zero, ~$0/month)
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

- **App Service for web, Container Apps Jobs for scans** — the web tier needs an always-warm, memory-heavy replica with deployment slots, which App Service does natively. The role/operations scans are bursty and benefit from cron scheduling + scale-to-zero, so they run as Container Apps Jobs (a single writer by construction) instead of a co-located worker.
- **P0v3 plan** — smallest Premium V3 (VNet integration, AlwaysOn, 5 free slots).
- **Two user-assigned MIs (least privilege)** — one *web* identity shared by the production + staging + ppe slots and mapped to a **SELECT-only** PostgreSQL role; one *scan* identity shared by both scan Jobs and mapped to an **owner / read-write** role. The scan identity is the sole schema owner (it alone runs `create_all`), so there is no ownership race; the web role is granted a one-directional default `SELECT` on the scan-owned tables. Each identity gets its own AcrPull grant.
- **PostgreSQL Flexible (not Single)** — better price/perf and Entra ID auth. B1ms is enough for the workload (< 5 RPS, ~200 MB data).
- **Entra ID for DB auth** — the app opens password-less, token-based connections; rotation is automatic via MSI tokens. The admin password is only needed at first deploy to provision the AAD-mapped role (see [§ Security notes](#security-notes)).
- **Slot-sticky config** — `APP_ENVIRONMENT_NAME` is registered in `slotConfigNames`, so a slot swap does NOT carry the telemetry label with the code. All slots share the one read-only web PG role (`MSI_DB_USER` is identical everywhere), so it needs no slot pinning. The web tier never scans (the image runs uvicorn only); the role + operations scans run exclusively in the Container Apps Jobs, which are single-writer by construction — so there is no scanner to keep off the staging slot anymore.

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

Total wall time: **~45–60 min** (PostgreSQL provisioning and the scan
bootstrap dominate). Run every step from `infra/`.

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
> pick something short and distinctive (3-15 alphanumeric). The ACR and
> PostgreSQL names are lowercased automatically (and hyphens stripped for
> ACR), so any casing is safe.

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
`resourceGroups/$RG_NAME`. Any "Unsupported" role-assignment entries
are normal — they reference managed-identity principalIds that only
exist after the apps are deployed.

### 4. Deploy

```bash
./scripts/deploy.sh prod
```

One command, but `deploy.sh` runs it in **two passes** — a scan Job
(Container Apps Job) verifies it can pull its image the instant it is
created, yet the ACR that holds the image is created by this same deploy.
So the script provisions the registry first, pushes the image, then adds the
Jobs:

1. Validates the template against Azure (~10 s).
2. **Pass 1** (`deployScanJobs=false`) — RG, monitoring, PostgreSQL, App
   Service, and ACR (~10–15 min; PG is the slow step). Scan Jobs skipped.
3. **Build & push** the container image into the Pass-1 ACR via
   [`build-and-push-image.sh`](scripts/build-and-push-image.sh) (`az acr
   build`, tagged `rbaccatalog:latest`).
4. **Pass 2** (`deployScanJobs=true`) — Container Apps environment + the two
   scan Jobs, now that the image they pull exists. Incremental, so it only
   creates the Jobs.
5. Saves outputs to `infra/.deploy-outputs.json`.
6. Configures Entra ID auth on PostgreSQL via
   [`grant-postgres-aad-admin.sh`](scripts/grant-postgres-aad-admin.sh) —
   registers the web identity (SELECT-only) and the scan identity (owner) as
   PG roles, and grants the web role a default `SELECT` on the scan-owned
   tables.
7. **Bootstraps the data** — starts both scan Jobs once and waits for them, so
   the schema + data exist before the web first boots (the read-only web
   cannot create the schema, and its cache refuses to start on an empty
   database).
8. Restarts the App Service so it pulls the freshly pushed image against the
   now-populated database, then polls `/healthz` until the web tier is up
   (cold start rebuilds the in-memory cache, so it waits up to ~10 min).

> The image is built **between** the two passes, so the very first deploy
> takes a few minutes longer than a later redeploy. To ship a new build
> later, just re-run `./scripts/deploy.sh prod` — it rebuilds & repushes the
> image and redeploys incrementally. For an **app-image-only** redeploy
> (no infra changes) run the build helper directly, then restart the web
> tier:
>
> ```bash
> ./scripts/build-and-push-image.sh        # ACR read from .deploy-outputs.json
> az webapp restart -n "$(jq -r .appServiceName.value .deploy-outputs.json)" \
>                   -g "$(jq -r .resourceGroupName.value .deploy-outputs.json)"
> ```

### 5. Verify

```bash
URL=$(jq -r .appServiceUrl.value .deploy-outputs.json)

curl -fsS "$URL/healthz"                              # {"ok":true}
curl -fsS -o /dev/null -w "%{http_code}\n" "$URL"     # 200
```

Open `$URL` in a browser and you should see the role catalog — `deploy.sh`
already ran both scans during bootstrap, so it's populated. They refresh on
cron afterwards (role-scan every 2 h, operations-scan daily).

To re-run a scan on demand (rarely needed) — portal (Container Apps Job →
*Run now*) or CLI:

```bash
RG=$(jq -r .resourceGroupName.value .deploy-outputs.json)
az containerapp job start -n "${BASE_NAME}-role-scan"       -g "$RG"
az containerapp job start -n "${BASE_NAME}-operations-scan" -g "$RG"
```

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
its image with the read-only web user-assigned managed identity (`AcrPull`
granted on the registry by Bicep), so your pipeline only needs *push*
permission on the ACR — no admin credentials required.

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

## Region availability & quota (pre-flight checks)

`--what-if` is a **template diff only** — it never contacts the resource
providers, so it cannot predict failures from regional capacity, SKU offer
restrictions, or subscription quota. Those surface only at actual deploy
time. A few cheap checks up front save round-trips:

```bash
# PostgreSQL: is your tier/SKU even offered in the region?
az postgres flexible-server list-skus --location "$LOCATION" -o table

# App Service: is the plan SKU offered in the region?
az appservice list-locations --sku P0V3 -o table

# Global name availability (App Service + ACR names are globally unique)
az webapp list  --query "[?name=='${BASE_NAME}-app'].name"     -o tsv   # empty = free
az acr check-name --name "${BASE_NAME}registry" --query nameAvailable   # true = free
```

What these **cannot** catch:

- **Container Apps `AKSCapacityHeavyUsage`** — real-time AKS capacity in a
  region. Azure exposes no API for it; the only signal is to attempt the
  create. If a region is saturated, deploy the Container Apps tier
  elsewhere (see below).
- **App Service vCPU quota** (`SubscriptionIsOverQuotaForSku`) — Premium V3
  families need an explicit quota request per region. A fresh subscription
  often has `0` Pv3 vCPUs in a given region; request quota or fall back to a
  Basic/Standard SKU (separate quota bucket).

### Deploying tiers in different regions

When one region is restricted or out of capacity, you don't have to move the
whole stack. `main.bicep` exposes independent region knobs that each default
to `location`, so you can place individual tiers where capacity/offers allow:

| Parameter | Controls | Override when… |
|---|---|---|
| `location` | App Service + monitoring + the resource group | — (primary region) |
| `postgresLocation` | PostgreSQL Flexible Server | the tier/SKU is offer-restricted in `location` |
| `acrLocation` | Container Registry | — (rarely needed) |
| `containerAppsLocation` | Container Apps environment + scan Jobs | `location` returns `AKSCapacityHeavyUsage` |

`deploy.sh` forwards any ad-hoc `name=value` arguments as Bicep parameter
overrides, so you can mix regions on the command line:

```bash
# App Service in West Europe (has quota), PostgreSQL + scan Jobs in North Europe
./scripts/deploy.sh prod \
  postgresLocation=northeurope \
  containerAppsLocation=northeurope
```

---

## Troubleshooting


**`ResourceNameNotAvailable`** — the App Service / ACR / PG name is taken
globally. Pick a more distinctive `baseName`.

**`AKSCapacityHeavyUsage` (Container Apps environment)** — the region is
temporarily out of Container Apps capacity. Redeploy the Container Apps tier
elsewhere with `containerAppsLocation=<other-region>` (see
[§ Region availability & quota](#region-availability--quota-pre-flight-checks)).

**`SubscriptionIsOverQuotaForSku` (App Service)** — no vCPU quota for that
SKU family in the region (Premium V3 often starts at `0`). Request quota or
deploy a Basic/Standard SKU via `appServicePlanSku=B1` (separate quota
bucket; note Basic has no deployment slots, so also pass `deploySlots=false`).

**`LocationIsOfferRestricted` (PostgreSQL)** — the tier/SKU isn't offered in
the region. Check `az postgres flexible-server list-skus --location <loc>`
and set `postgresLocation=<region-that-offers-it>`.

**`InvalidResourceLocation` on retry** — an earlier failed deploy left an
orphaned resource (e.g. a `Failed`-state Container Apps environment or its
identity) pinned to the old region, and Azure won't relocate it. Delete the
leftovers in that region, then re-run.

**Container won't start (5xx)** — the image isn't in the ACR yet. Re-run
`./scripts/deploy.sh prod` (or `./scripts/build-and-push-image.sh` followed by
an `az webapp restart`).

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
│   ├── identity.bicep                 # one user-assigned MI (instantiated twice: web + scan)
│   ├── acr.bicep                      # Container Registry + AcrPull (web + scan identities)
│   ├── postgres.bicep                 # PG Flexible Server + AAD auth
│   ├── appservice.bicep               # Plan + app + optional staging/ppe slots
│   └── containerappjobs.bicep         # Container Apps env + role/operations cron Jobs
└── scripts/
    ├── deploy.sh                      # orchestrates: infra → image → Jobs → DB grant
    ├── build-and-push-image.sh        # build + push the app image to ACR (az acr build)
    └── grant-postgres-aad-admin.sh    # PG Entra ID auth + grants (web + scan roles)
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

