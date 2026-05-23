# Run Locally

Native Python is the recommended path. Docker is a one-liner alternative — see the [Docker](#docker-alternative) section at the bottom.

## Prerequisites

- Python 3.12
- Git
- Optional: Azure CLI (`az login`) — only needed to populate the catalog with live data from Azure

## Setup

```bash
git clone https://github.com/atomassi/rbac-catalog.git
cd rbac-catalog
python3.12 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## Run

The app is made of **two processes** that run independently. You'll typically run them in two terminals.

| Process | Command | What it does |
|---|---|---|
| **Web server** | `uvicorn azurerbac.web.app:app --host 0.0.0.0 --port 8000 --reload` | Serves the UI + API at <http://localhost:8000>. Doesn't need Azure auth. |
| **Background worker** | `python -m azurerbac.backgroundjobs.worker` | Pulls Azure roles + operations on a schedule and writes them to the database. Needs Azure auth. |

The web server works on its own — the UI loads, search works — but the catalog will be empty until the worker has scanned at least once.

If you want to run both in one terminal:

```bash
python -m azurerbac.backgroundjobs.worker &
uvicorn azurerbac.web.app:app --host 0.0.0.0 --port 8000 --reload
```

> [!TIP]
> First startup loads ColBERT + sentence-transformers + PyTorch — several hundred MB. To skip the heavy engines and boot in a few seconds, set `ENABLED_AI_ENGINES=tfidf`. Comma-separate to enable more (e.g. `tfidf,semantic`).

## Populating the catalog

The worker authenticates with [`DefaultAzureCredential`](https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.defaultazurecredential), which tries environment variables → workload identity → managed identity → Azure CLI. The interactive browser flow is disabled by default, so for local development run `az login` once before starting the worker.

You do **not** need Reader on a specific subscription — the app fetches role definitions from the tenant-scoped RBAC API endpoint (`/providers/Microsoft.Authorization/roleDefinitions`), which any principal authenticated to your tenant can read.

By default the worker runs a scan immediately on startup (`RUN_SCAN_ON_STARTUP=true`), then re-polls automatically (roles every 2h, operations every 24h). To disable the startup scan and only run on the regular schedule:

```bash
export RUN_SCAN_ON_STARTUP=false
python -m azurerbac.backgroundjobs.worker
```

## Environment variables

The app reads all configuration from environment variables. For local runs, an optional `.env` file in the working directory is loaded automatically via `python-dotenv`. The most useful settings:

| Variable | Default | Purpose |
|---|---|---|
| `DB_CONNECTION_STRING` | `sqlite+aiosqlite:///./azurerbac.db` | Switch to `postgresql+asyncpg://...` to use Postgres instead. |
| `LOG_LEVEL` | `INFO` | Set to `DEBUG` for verbose logs. |
| `ENABLED_AI_ENGINES` | `crossencoder,colbert,semantic,llm,rag,hyde,tfidf` | Comma-separated subset of `tfidf,semantic,colbert,crossencoder,llm,rag,hyde,hybrid`. Set to `tfidf` for fast boot. |
| `RUN_SCAN_ON_STARTUP` | `true` | Worker runs a roles scan immediately on startup. Set to `false` to only run on the regular poll interval. |
| `RUN_OPERATIONS_SCAN_ON_STARTUP` | `true` | Same, for operations. |
| `ROLE_SCAN_ENABLED` | `true` | Set to `false` to disable the worker's role scanner. |
| `OPERATIONS_SCAN_ENABLED` | `true` | Set to `false` to disable the worker's operations scanner. |
| `ROLES_POLL_INTERVAL_SECONDS` | `7200` | How often the worker re-scans roles. |
| `OPERATIONS_POLL_INTERVAL_SECONDS` | `86400` | How often the worker re-scans operations. |

## Docker (alternative)

If you don't want to install Python locally:

```bash
docker build -t azurerbac:local --build-arg VERSION=local-dev .
docker run --rm -p 8000:8000 azurerbac:local
```

The container runs both the web server and the worker side-by-side. The worker won't have Azure credentials by default, so the catalog will stay empty — pass a service principal to fix that:

```bash
docker run --rm -p 8000:8000 \
  -e AZURE_TENANT_ID=... -e AZURE_CLIENT_ID=... -e AZURE_CLIENT_SECRET=... \
  azurerbac:local
```

Or skip the worker entirely and mount a SQLite file you populated natively:

```bash
docker run --rm -p 8000:8000 \
  -e ROLE_SCAN_ENABLED=false -e OPERATIONS_SCAN_ENABLED=false \
  -v "$PWD/azurerbac.db:/app/azurerbac.db" \
  azurerbac:local
```

## Troubleshooting

**`AzureCliCredential authentication failed`** — run `az login`. The worker uses your CLI session.

**Empty catalog after a scan that looked successful** — set `LOG_LEVEL=DEBUG` and watch the worker logs. The fetcher raises `EmptyFetchResultError` when Azure returns zero rows, which usually means a permissions/auth issue rather than a real empty result.

**Wipe and start over** — delete `./azurerbac.db` (SQLite) or drop the database (`DROP DATABASE azurerbac; CREATE DATABASE azurerbac;` for Postgres). The schema is recreated on next boot.
