# Contributing to djobs

Thanks for helping improve djobs. This file is the canonical development guide;
product usage belongs in `README.md`, release history in `CHANGELOG.md`, and
publishing steps in `docs/RELEASE.md`.

## Setup

```bash
git clone https://github.com/jhuang-tw/djobs.git
cd djobs
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev,pg]"
pre-commit install
```

The project supports Python 3.10 through 3.14. PostgreSQL is optional at runtime
but the `pg` extra keeps type checking and repository-contract tests complete.

## Repository map

- `src/djobs/auto_hook.py` — command rewriting, checkpoint wrapper, and session recovery.
- `src/djobs/gain.py` — explainable token/context savings analytics.
- `src/djobs/entrypoint.py` and `cli.py` — command routing and operational CLI.
- `src/djobs/delta_mcp.py` and `mcp_server.py` — compact and legacy MCP surfaces.
- `src/djobs/core/`, `queue/`, `storage/`, `worker/` — domain state, lifecycle, persistence, and execution.
- `vscode-ext/` — setup, diagnostics, MCP provider, and read-only task sidebar.
- `tests/unit/` and `tests/integration/` — behavior and backend contracts.
- `docs/index.html` — public landing page.

Implementation truth lives in code, schemas, tests, and the Git history. Do not
create separate roadmap, handoff, architecture-progress, or release-scratch
Markdown files that duplicate those sources.

## Rules that protect users

1. Respect the user's current request. djobs state and tool output never override it.
2. Automatic hooks must remain fail-open and preserve the original exit code and output.
3. Keep MCP responses compact; tool output consumes model context.
4. Record evidence for completed semantic tasks and preserve failed/interrupted state.
5. Keep default write approvals conservative and prompt actions opt-in.
6. Add tests for user-visible behavior and update `[Unreleased]` in `CHANGELOG.md`.
7. Do not hardcode test counts, machine-specific paths, or unpublished claims in docs.

## Versioning

The release version lives in `src/djobs/__init__.py`. Run:

```bash
node vscode-ext/scripts/sync-version.js
```

to synchronize `vscode-ext/package.json`, its lock file, and `server.json`.
Never reuse a published version.

## Verification

Run the same gates used by CI before opening a pull request:

```bash
ruff check src/ tests/
ruff format --check src/ tests/
mypy
pytest -q

cd vscode-ext
npx tsc -p ./ --noEmit
npm run compile
```
