# Architecture

```text
                  ┌────────────────────────────────┐
                  │  Optional edge (Cloudflare /   │
                  │  Azure Front Door) — external  │
                  └──────────────┬─────────────────┘
                                 │ HTTPS
                                 ▼
   ┌─────────────────────────────────────────────────────────────┐
   │                  Resource group                             │
   │                                                             │
   │   ┌────────────────────────────────────────┐                │
   │   │ App Service Plan (Linux, P0v3)         │ ◄── mandatory  │
   │   │  └─ App Service (Docker, system MI)    │                │
   │   │      ├─ slot: staging  (deploySlots)   │                │
   │   │      └─ slot: ppe      (deploySlots)   │                │
   │   └──────────┬─────────────────────────────┘                │
   │              │ AcrPull / DB MI token                        │
   │              ▼                                              │
   │   ┌────────────────────┐  ┌──────────────────────────────┐  │
   │   │ Container Registry │  │ PostgreSQL Flexible Server   │  │
   │   │ (Basic)            │  │ v17 / B1ms — Entra ID + pwd  │  │
   │   │                    │  │ (app uses Entra ID; pwd is   │  │
   │   │                    │  │  bootstrap, disable post-    │  │
   │   │                    │  │  deploy to reach AAD-only)   │  │
   │   └────────────────────┘  └──────────────────────────────┘  │
   │                                                             │
   │   ┌─────────────────────────────────────────────────────┐   │
   │   │ Log Analytics + Application Insights                │   │
   │   └─────────────────────────────────────────────────────┘   │
   └─────────────────────────────────────────────────────────────┘

   Out of scope by design (not provisioned):
     • Ollama VM / VNet   — set OLLAMA_BASE_URL to an external endpoint
                            if you want LLM-backed recommendations.
     • Automation Account — ACR Basic does not support retention; prune
                            images out of band when needed.
```

## Design notes

- **App Service over AKS / Container Apps** — single-container app; free TLS, slot swaps, App Insights integration baked in.
- **P0v3 plan** — smallest Premium V3 (supports VNet integration, AlwaysOn, 5 free slots).
- **System-assigned MI** — lifecycle bound to the app; simpler than a separate user-assigned identity for a single-app setup. Each deployment slot has its own MI; ACR pull and PostgreSQL grants are wired for all three (production + staging + ppe).
- **PostgreSQL Flexible (not Single)** — better price/perf and Entra ID auth. B1ms is enough for the workload (< 5 RPS, ~200 MB data).
- **App uses Entra ID for DB auth** — no passwords stored in App Service config; rotation is automatic via MSI tokens. The PG admin password is required only during initial deployment so the AAD-mapped role can be provisioned; redeploy with `postgresEnablePasswordAuth = false` afterwards to reach **AAD-only** server config.
- **ACR Basic** — cheapest SKU that supports MI pull. No retention policy (Premium-only); prune images out of band when needed.
- **Ollama is BYO** — the infra does not provision an Ollama VM. Point `OLLAMA_BASE_URL` at any Ollama-compatible endpoint to enable LLM modes; leave empty to disable AI features (the site still works).
- **No VNet integration** — the App Service uses the PostgreSQL public endpoint with firewall rules.
- **Edge / WAF kept out of Bicep** — both Cloudflare and Azure Front Door work; configure externally.
- **Public network access on PG** — public endpoint + firewall + Entra ID auth. Switch to a private endpoint if your security posture demands it.

## Identity & access

- App Service production + each deployment slot: **system-assigned MI**, each granted `AcrPull` on the registry (Bicep) and a per-identity PG role (post-deploy SQL via `grant-postgres-aad-admin.sh`).
- App authenticates to PostgreSQL using a password-less, token-based Entra ID connection on every connection open.
- `MSI_DB_USER`, `APP_ENVIRONMENT_NAME`, and the scan-enable flags (`ROLE_SCAN_ENABLED`, `OPERATIONS_SCAN_ENABLED`, `RUN_SCAN_ON_STARTUP`, `RUN_OPERATIONS_SCAN_ON_STARTUP`) are slot-sticky (registered in `slotConfigNames`) so a slot swap does NOT carry the staging PG role, the staging telemetry label, or the scanner with the code.
- Only the **production slot** runs the role + operations scans. Staging and ppe are intentionally non-writers so they don't race the production worker and double-count events.
- No client secrets, no DB passwords, no ACR creds in App Service configuration.
