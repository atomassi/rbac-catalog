# Claude Instructions

Additional rules for Claude / Copilot agents working in this repository.

## Environment

- **Always** activate the virtual environment before running any terminal command: `source .venv/bin/activate`

## Git Rules

- **NEVER** use `git add -A`. Always use `git add -u` (tracked files only).

## Code Quality

- **Before committing**, always run `ruff check --fix` and `ruff format` on changed Python files to satisfy pre-commit hooks.
- Write code that passes `ruff` linting (e.g. use `{a, b}` set comparisons instead of `a == x or b == x`, follow PLR/PLW rules).
- Write code that passes `pyright` in `standard` mode — all functions must have complete type hints.
