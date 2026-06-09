# Contributing to Azure RBAC Catalog

Thanks for taking the time to contribute! This guide covers how to set up a
local environment, the checks your change must pass, and how to open a pull
request.

## Code of Conduct

Be respectful and constructive. Harassment, personal attacks, and other
unwelcoming behaviour are not tolerated in issues, pull requests, or any other
project space.

## Ways to contribute

- **Report a bug** — open an issue with steps to reproduce, expected vs. actual
  behaviour, and your environment (OS, Python version).
- **Suggest a feature** — open an issue describing the use case before sending a
  large PR, so we can agree on the approach.
- **Send a fix** — small, focused pull requests are easiest to review.
- **Report a vulnerability** — follow [SECURITY.md](SECURITY.md). You may open a
  GitHub issue for awareness, but **do not include working exploit details**.

## Development setup

Requires **Python 3.12, 3.13, or 3.14** (all supported and tested in CI — see
[`docs/run-local.md`](../docs/run-local.md) for details) and Node 20 for the
frontend tooling.

```bash
git clone https://github.com/atomassi/rbac-catalog.git
cd rbac-catalog
python3.12 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
npm ci
```

Run the app locally (full instructions in [`docs/run-local.md`](../docs/run-local.md)):

```bash
# Boot fast by disabling the heavy AI engines:
ENABLED_AI_ENGINES=tfidf uvicorn rbaccatalog.web.app:app --reload --port 8000
```

## Before you open a pull request

Run the same checks CI runs. All of these must pass:

```bash
# Python lint + format
ruff check rbaccatalog/
ruff format --check rbaccatalog/

# Static type checking (standard mode)
pyright rbaccatalog/

# Python unit tests with coverage (must stay >= 80%, same gate as CI)
pytest tests/ -q --cov=rbaccatalog --cov-report=term --cov-fail-under=80

# JavaScript type check + unit tests
npm run check:js
npm run test:js

# End-to-end tests (Playwright)
npm run test:e2e
```

> Pre-commit and pre-push hooks run a subset of these automatically. Do not
> bypass them with `--no-verify`.

## Coding guidelines

The conventions enforced in this repo are documented in
[`.github/copilot-instructions.md`](copilot-instructions.md). The
essentials:

- **Async-only** — no synchronous I/O. Use `httpx` and `asyncio.sleep()`, never
  `requests` or `time.sleep()`.
- **Full type hints** on every function; Pyright runs in `standard` mode.
- **Absolute imports** from `rbaccatalog.*` — no relative imports.
- **Respect module boundaries** — dependencies point inward
  (`web → matching → core`); `core` must not import higher layers.
- **No new dependencies** without discussing it in an issue first.
- Use the `telemetry.logging` module instead of `print()`.
- SQLAlchemy 2.0 `select()` syntax; Pydantic v2 `.model_dump()`.
- Production-ready code only — no TODOs or placeholders.

Match the existing naming conventions: `snake_case` for functions/variables,
`PascalCase` for classes, `UPPER_SNAKE_CASE` for constants, `kebab-case` in URLs.

## Commit and pull request process

1. Create a topic branch off `main`.
2. Keep commits focused; write clear, imperative commit messages
   (e.g. `Add diff view for deprecated roles`).
3. Update or add tests for any behaviour you change — test files mirror the
   source layout (`tests/test_<module>.py`).
4. Update relevant docs (`README.md`, `docs/`) when behaviour or setup changes.
5. Open a PR against `main` and fill in the template. Link any related issue.
6. Ensure all CI checks are green. Maintainers review and may request changes.

## License

By contributing, you agree that your contributions will be licensed under the
[MIT License](../LICENSE) that covers this project.
