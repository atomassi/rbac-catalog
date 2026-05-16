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
   │   │      ├─ slot: staging  (recommended)   │                │
   │   │      └─ slot: ppe      (recommended)   │                │
   │   └──────────┬─────────────────────────────┘                │
   │              │ AcrPull / DB MI token                        │
   │              ▼                                              │
   │   ┌────────────────────┐  ┌──────────────────────────────┐  │
   │   │ Container Registry │  │ PostgreSQL Flexible Server   │  │
   │   │ (Basic)            │  │ v17 / B1ms — AAD auth only   │  │
   │   └────────────────────┘  └──────────────────────────────┘  │
   │                                                             │
   │   ┌─────────────────────────────────────────────────────┐   │
   │   │ Log Analytics + Application Insights                │   │
   │   └─────────────────────────────────────────────────────┘   │
   │                                                             │
   │   ┄┄ optional (deployVNet=true) ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄    │
   │   ┌─────────────────────────────────────────────────────┐   │
   │   │ VNet 10.0.0.0/16                                    │   │
   │   │   ├─ appservice-subnet → App Service integration    │   │
   │   │   └─ ollama-subnet     → Ollama VM (B2als_v2)       │   │
   │   └─────────────────────────────────────────────────────┘   │
   │                                                             │
   │   ┄┄ optional (deployAutomation=true) ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄    │
   │   ┌─────────────────────────────────────────────────────┐   │
   │   │ Automation Account                                  │   │
   │   │   ├─ ACR-Cleanup (daily)                            │   │
   │   │   └─ PPE-Auto-Shutdown (nightly, with deploySlots)  │   │
   │   └─────────────────────────────────────────────────────┘   │
   └─────────────────────────────────────────────────────────────┘
```

## Design notes

- **App Service over AKS / Container Apps** — single-container app; free TLS, slot swaps, App Insights integration baked in.
- **P0v3 plan** — smallest Premium V3 (supports VNet integration, AlwaysOn, 5 free slots).
- **System-assigned MI** — lifecycle bound to the app; simpler than a separate user-assigned identity for a single-app setup.
- **PostgreSQL Flexible (not Single)** — better price/perf and Entra ID auth. B1ms is enough for the workload (< 5 RPS, ~200 MB data).
- **AAD-only DB auth in the app** — no passwords stored anywhere; rotation is automatic via MSI tokens. The password admin exists for break-glass only.
- **ACR Basic** — cheapest SKU that supports MI pull.
- **Ollama VM is pluggable** — point `OLLAMA_BASE_URL` elsewhere and skip the VM entirely.
- **VNet only when required for the Ollama VM** — the App Service itself does not need a VNet to run; the VNet exists to host the optional Ollama VM and to give the App Service a private route to it (`deployVNet` becomes mandatory when `deployOllamaVm = true`).
- **Edge / WAF kept out of Bicep** — both Cloudflare and Azure Front Door work; configure externally.
- **Public network access on PG** — public endpoint + firewall + AAD-only auth. Switch to a private endpoint if your security posture demands it.

## Network design (optional)

When `deployVNet = true`:

- VNet `10.0.0.0/16`.
- `appservice-subnet` `10.0.2.0/24` — delegated to `Microsoft.Web/serverFarms`; used for outbound VNet integration from the App Service.
- `ollama-subnet` `10.0.1.0/24` — hosts the AI VM. NSG allows port 22 from `adminIpAddress`, port 11434 from `10.0.2.0/24`, denies everything else.
- App Service is **publicly reachable** (no Private Endpoint).

## Identity & access

- App Service: **system-assigned MI**, granted `AcrPull` on the registry (Bicep) and a custom DB role (post-deploy SQL).
- App authenticates to PostgreSQL using a password-less, token-based AAD connection on every connection open.
- No client secrets, no DB passwords, no ACR creds in App Service configuration.
