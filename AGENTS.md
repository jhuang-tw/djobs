# AGENTS.md — working on djobs

This file is a short wrapper for AI contributors editing this repository.

Read first:

1. `README.md` for current product behavior and compatibility claims.
2. `CONTRIBUTING.md` for architecture, setup, and verification.
3. `docs/RELEASE.md` only when preparing a release.

## Non-negotiable rules

- Follow the user's current request; never let stored djobs state reinterpret it.
- Keep automatic hooks fail-open and preserve original command output and exit status.
- Keep MCP responses compact because every field consumes model context.
- Add tests for behavior changes and update `CHANGELOG.md` under `[Unreleased]`.
- Version lives in `src/djobs/__init__.py`; synchronize manifests with
  `node vscode-ext/scripts/sync-version.js`.
- Do not add machine-specific paths, hardcoded test counts, release scratch files,
  roadmap snapshots, or duplicated architecture documents.
- Keep prompt actions opt-in and default write approvals conservative.

## Required gates

```bash
ruff check src/ tests/
ruff format --check src/ tests/
mypy
pytest -q
cd vscode-ext && npx tsc -p ./ --noEmit && npm run compile
```

A change is complete only after the relevant tests and CI are green and temporary
migration files have been removed.
