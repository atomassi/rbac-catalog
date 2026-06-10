# Run Locally

Native Python is the recommended path. Docker is a one-liner alternative — see the [Docker](#docker-alternative) section at the bottom.

The commands below use a Bash / Zsh shell (macOS, Linux, WSL). For native Windows PowerShell, see the short [Windows notes](#windows-powershell-notes) at the bottom for the equivalent commands.

## Prerequisites

- **Python 3.12, 3.13, or 3.14** — all supported and tested. Verify with `python3 --version` before continuing. The examples below use `python3.12`; substitute `python3.13`/`python3.14` if you prefer a newer interpreter.
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
| **Web server** | `uvicorn rbaccatalog.web.app:app --host 0.0.0.0 --port 8000 --reload` | Serves the UI + API at <http://localhost:8000>. Doesn't need Azure auth. |
| **Scan job** | `python -m rbaccatalog.backgroundjobs.scan_once` | Pulls Azure roles + operations once and writes them to the database, then exits. Needs Azure auth. |

The web server works on its own — the UI loads, search works — but the catalog will be empty until a scan has run at least once.

If you want to run both in one terminal:

```bash
python -m rbaccatalog.backgroundjobs.scan_once
uvicorn rbaccatalog.web.app:app --host 0.0.0.0 --port 8000 --reload
```

> [!TIP]
> First startup loads sentence-transformers + PyTorch — several hundred MB. To skip the heavy engines and boot in a few seconds, set `ENABLED_AI_ENGINES=tfidf`. Comma-separate to enable more (e.g. `tfidf,semantic`).

## Populating the catalog

When running locally, each scan authenticates with [`DefaultAzureCredential`](https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.defaultazurecredential), which tries environment variables → workload identity → managed identity → Azure CLI. The interactive browser flow is disabled by default, so for local development run `az login` once before starting a scan. When deployed to Azure the scan Jobs use a user-assigned managed identity instead (`USE_MANAGED_IDENTITY=true` with `AZURE_CLIENT_ID`), so no `az login` is needed.

You do **not** need Reader on a specific subscription — the app fetches role definitions from the tenant-scoped RBAC API endpoint (`/providers/Microsoft.Authorization/roleDefinitions`), which any principal authenticated to your tenant can read.

Each invocation runs the requested scans once and exits. Pass a job name to run just one:

```bash
python -m rbaccatalog.backgroundjobs.scan_once role-scan
python -m rbaccatalog.backgroundjobs.scan_once operations-scan
```

## Environment variables

The app reads all configuration from environment variables. For local runs, an optional `.env` file in the working directory is loaded automatically via `python-dotenv`. The most useful settings:

| Variable | Default | Purpose |
|---|---|---|
| `DB_CONNECTION_STRING` | `sqlite+aiosqlite:///./rbaccatalog.db` | Switch to `postgresql+asyncpg://...` to use Postgres instead. |
| `LOG_LEVEL` | `INFO` | Set to `DEBUG` for verbose logs. |
| `ENABLED_AI_ENGINES` | `crossencoder,semantic,llm,rag,hyde,tfidf` | Comma-separated subset of `tfidf,semantic,crossencoder,llm,rag,hyde,hybrid`. Set to `tfidf` for fast boot. |
| `ROLE_SCAN_ENABLED` | `true` | Set to `false` to make the role scan a no-op. |
| `OPERATIONS_SCAN_ENABLED` | `true` | Set to `false` to make the operations scan a no-op. |

## Docker (alternative)

If you don't want to install Python locally:

```bash
docker build -t rbaccatalog:local --build-arg VERSION=local-dev .
docker run --rm -p 8000:8000 rbaccatalog:local
```

The container runs both the web server and the worker side-by-side. The worker won't have Azure credentials by default, so the catalog will stay empty — pass a service principal to fix that:

```bash
docker run --rm -p 8000:8000 \
  -e AZURE_TENANT_ID=... -e AZURE_CLIENT_ID=... -e AZURE_CLIENT_SECRET=... \
  rbaccatalog:local
```

Or skip the worker entirely and mount a SQLite file you populated natively:

```bash
docker run --rm -p 8000:8000 \
  -e ROLE_SCAN_ENABLED=false -e OPERATIONS_SCAN_ENABLED=false \
  -v "$PWD/rbaccatalog.db:/app/rbaccatalog.db" \
  rbaccatalog:local
```

## Troubleshooting

**`AzureCliCredential authentication failed`** — run `az login`. The worker uses your CLI session.

**Empty catalog after a scan that looked successful** — set `LOG_LEVEL=DEBUG` and watch the worker logs. The fetcher raises `EmptyFetchResultError` when Azure returns zero rows, which usually means a permissions/auth issue rather than a real empty result.

**Wipe and start over** — delete `./rbaccatalog.db` (SQLite) or drop the database (`DROP DATABASE azurerbac; CREATE DATABASE azurerbac;` for Postgres). The schema is recreated on next boot.

## Windows (PowerShell) notes

The app runs on native Windows — no Linux-only dependencies. The shell syntax differs from the Bash examples above, and Python is usually installed as `py` (or `python`) rather than `python3.12`:

| Bash / Zsh | PowerShell |
|---|---|
| `python3.12 -m venv .venv` | `py -3.12 -m venv .venv` |
| `source .venv/bin/activate` | `.\.venv\Scripts\Activate.ps1` |
| `export VAR=value` | `$env:VAR = "value"` |
| `python -m rbaccatalog.backgroundjobs.scan_once &` | Open a second PowerShell window, or `Start-Process python -ArgumentList '-m','rbaccatalog.backgroundjobs.scan_once'` |
| `-v "$PWD/rbaccatalog.db:/app/rbaccatalog.db"` | `-v "${PWD}\rbaccatalog.db:/app/rbaccatalog.db"` |

