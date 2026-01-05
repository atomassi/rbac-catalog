# Copilot Instructions

This document provides context and guidelines for GitHub Copilot when working in this repository.

## TL;DR for Copilot

- **Async-only** codebase — no blocking I/O
- **Full type hints** required on all functions
- **Reuse existing patterns** — check before creating new abstractions
- **No new dependencies** without explicit request
- **Respect module boundaries** — see Architectural Boundaries
- **Production-quality code only** — no TODOs, no placeholders

> Copilot suggestions that violate these rules should be considered incorrect.

## Copilot Behavior Guidelines

When generating or modifying code, GitHub Copilot should:

- Prefer existing patterns and utilities over introducing new abstractions
- Search for similar implementations in this repository (especially in the same module or layer) before writing new code
- Avoid introducing new dependencies unless explicitly requested (including transitive or optional dependencies)
- Match existing naming conventions and file structure exactly
- Generate production-ready code (no TODOs, no placeholders)
- Assume this is a long-lived, audited codebase (clarity > cleverness)
- When creating new API endpoints, reference `azurerbac/web/dependencies.py` for auth and database injection patterns

## Repository Summary

**Azure RBAC Catalog** is a comprehensive catalog and monitoring tool for Azure built-in RBAC roles. It enables users to:
- Browse 800+ Azure built-in roles with full permission details
- Search 20,000+ resource provider operations
- Track when Microsoft adds, modifies, or deprecates roles
- Get AI-powered least-privilege role recommendations (8 different modes)
- View diffs between role versions
- Get least-privilege role suggestions based on permission requirements 

## Tech Stack

| Layer | Technologies |
|-------|-------------|
| **Backend** | Python 3.12+, FastAPI, SQLAlchemy 2.0, Pydantic v2 |
| **Frontend** | Jinja2 templates, Tailwind CSS, Alpine.js (MPA, not SPA) |
| **Database** | PostgreSQL 14+ (asyncpg for async, aiosqlite for testing) |
| **AI/ML** | sentence-transformers, ColBERT, Ollama (Qwen fine-tuned) |
| **Testing** | pytest (unit), Playwright (E2E) |
| **Linting** | Ruff (linting + formatting), Pyright (type checking) |
| **CI/CD** | GitHub Actions, Docker, Azure Container Registry |
| **Hosting** | Azure App Service, Cloudflare CDN |

## Project Structure

| Module | Purpose |
|--------|---------|
| `airecommender/` | AI recommendation engines (8 modes) |
| `azure/` | Azure SDK integration (roles, operations) |
| `backgroundjobs/` | Scheduled tasks and monitoring |
| `cache/` | In-memory caching layer |
| `core/` | Database models, constants, utilities |
| `matching/` | Role matching and recommendation service |
| `telemetry/` | OpenTelemetry logging and metrics |
| `web/` | FastAPI app, routes, templates |

## Architectural Boundaries

Copilot must respect the following module boundaries:

- `web/` — Request handling and presentation logic only
- `core/` — Domain logic and database models
- `azure/` — External Azure API interaction only
- `airecommender/` — AI logic; must not depend on web or FastAPI
- `matching/` — Role matching logic; orchestrates airecommender and cache
- `backgroundjobs/` — Scheduled tasks; may import from core and azure
- `cache/` — May not import from `web/`
- Database models must not import FastAPI or web-layer code

Dependencies must point inward:
- `web → matching → core` ✔️
- `web → core` ✔️
- `backgroundjobs → core` ✔️
- `airecommender → core` ✔️
- `core` must not depend on higher layers ❌

## Anti-Patterns to Avoid

❌ **No synchronous I/O** — Never use `requests` or `time.sleep()`. Use `httpx` and `asyncio.sleep()`  
❌ **No bare exceptions** — Never use `except:`. Always catch specific exceptions  
❌ **No manual JSON parsing** — Don't use `json.loads(request.body)`. Use Pydantic model injection  
❌ **No `print()`** — Use the `telemetry.logging` module for all output  
❌ **No `session.query()`** — Use SQLAlchemy 2.0 `select()` syntax  
❌ **No `.dict()`** — Use Pydantic v2 `.model_dump()` method  
❌ **No relative imports** — Use absolute imports from `azurerbac.*`  
❌ **No global state** — Avoid module-level mutable state except where explicitly designed (e.g., cache layer)

## Key Patterns & Conventions

### Async/Await

⚠️ **Async is mandatory** — Do NOT introduce synchronous database, HTTP, or file I/O. All FastAPI routes, DB access, and background jobs must be async.

All async functions must be awaited; do not fire-and-forget coroutines.

Use `async def` for route handlers and database access:

```python
async def get_roles(db: AsyncSession) -> list[Role]:
    result = await db.execute(select(Role))
    return list(result.scalars().all())
```

### Dependency Injection

FastAPI dependencies are defined in `azurerbac/web/dependencies.py`:

```python
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session

@router.get("/roles")
async def list_roles(db: AsyncSession = Depends(get_db)):
    ...
```

### Type Hints

All functions must have complete type hints. Pyright enforces this in `standard` mode:

```python
def compute_similarity(query: str, role: RoleDefinition) -> float:
    ...
```

### Docstrings

Use Google-style docstrings for public functions:

```python
def find_matching_roles(query: str, limit: int = 10) -> list[RoleMatch]:
    """Find roles matching the given query.

    Args:
        query: Natural language description of required permissions.
        limit: Maximum number of results to return.

    Returns:
        List of matching roles with confidence scores.
    """
```

### Error Handling

Use structured error responses with appropriate HTTP status codes:

```python
from fastapi import HTTPException

if not role:
    raise HTTPException(status_code=404, detail="Role not found")
```

`HTTPException` may only be raised in the `web/` layer.

### Testing Patterns

- Use pytest fixtures from `tests/conftest.py` for shared setup
- Mock external Azure API calls using `unittest.mock`
- Use `pytest.mark.asyncio` for async tests (auto mode is enabled)
- Test files mirror the source structure: `test_<module_name>.py`
- When adding logic, prefer updating existing tests rather than creating new test helpers

### Import Style

Use absolute imports from the package root:

```python
from azurerbac.core.models import Role, RoleHistory
from azurerbac.cache.app_cache import get_cached_roles
from azurerbac.web.dependencies import get_db
```

### Naming Conventions

- **Variables/functions**: `snake_case`
- **Classes**: `PascalCase`
- **Constants**: `UPPER_SNAKE_CASE`
- **Private methods**: `_leading_underscore`
- **API endpoints**: `kebab-case` in URLs

## Important Configuration Files

- `pyproject.toml` — Ruff, Pyright, pytest configuration
- `requirements.txt` — Python dependencies (pinned versions)
- `tailwind.config.js` — Tailwind CSS configuration
- `playwright.config.js` — Playwright E2E test configuration
- `.github/dependabot.yml` — Automated dependency updates

## Database Models

Key SQLAlchemy models in `azurerbac/core/models.py`:

- `Role` — Tracks role identity and current state (role_id, role_name, status)
- `RoleHistory` — Historical versions with role_json, diff_json, event_type
- `RoleScanStatus` — Scan metadata (timestamp, additions, updates, deletions)
- `Operation` — Resource provider operations (name, provider, is_data_action)
- `OperationScanStatus` — Operation scan metadata

## AI Recommendation Modes

The recommender supports 8 modes with increasing sophistication:

1. **TF-IDF** — Enhanced keyword matching with BM25
2. **Semantic** — Sentence embedding cosine similarity
3. **ColBERT** — Token-level late interaction
4. **Cross-Encoder** — Neural reranking of candidates
5. **LLM** — Fine-tuned Qwen model inference
6. **RAG** — Retrieval-augmented generation
7. **HyDE** — Hypothetical document embeddings
8. **Hybrid** — Multi-stage pipeline combining modes

### AI Implementation Guidelines

- **TF-IDF/BM25** — Managed in `enhanced_tfidf.py`. Do not add neural logic here
- **Vector Search** — Use `sentence-transformers` in `role_recommender.py`
- **Orchestration** — `ai_recommender.py` is the only place for multi-stage (Hybrid) logic
- Prefer deterministic methods (TF-IDF, embeddings) over LLMs unless required
- LLMs must not be used for authorization or security decisions
- AI outputs must not be persisted without a deterministic identifier or version metadata

## Security Considerations

- All user inputs are sanitized before display (Jinja2 autoescaping)
- Rate limiting via `slowapi` on API endpoints
- SQL injection prevented by SQLAlchemy parameterized queries
- No secrets in code — use environment variables
- GitHub Actions use SHA-pinned actions for supply chain security

## Interaction Model

When asked to design or modify functionality:

- When modifying AI logic, explain the trade-offs between modes (Semantic vs. ColBERT vs. LLM) before writing code
- If a feature requires a new database table, suggest the SQLAlchemy model in `core/models.py` AND the migration approach
- If a change requires modifying multiple files, list all affected files in the summary
- Propose the minimal viable change first; do not suggest large rewrites unless explicitly requested
- Align with existing file/module structure
