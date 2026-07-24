# Contributing to djobs

Thanks for helping improve djobs. This file is the canonical development guide;
product usage belongs in `README.md`, release history in `CHANGELOG.md`, and
automatic publishing behavior in `docs/RELEASE.md`.

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

The project supports Python 3.10 through 3.14. PostgreSQL is optional at runtime,
but the `pg` extra keeps type checking and repository-contract tests complete.

## Repository map

- `src/djobs/workspace.py` — local repository and cross-shell identity resolution.
- `src/djobs/handoff.py` — compact workspace reads plus explicit checkpoint and handoff.
- `src/djobs/observations.py` and `lifecycle.py` — passive, bounded local observations.
- `src/djobs/host_hooks.py` and `setup_cli.py` — host-specific local adapters and safe setup.
- `src/djobs/coding_mcp.py` — the default compact coding MCP surface.
- `src/djobs/auto_hook.py` — legacy explicit command-checkpoint compatibility only; normal setup must not install it.
- `src/djobs/core/`, `queue/`, `storage/`, `worker/` — durable queue internals and optional general job execution.
- `vscode-ext/` — headless native MCP registration, passive adapter setup, and diagnostics.
- `scripts/prepare_auto_release.py` — deterministic SemVer, manifest, and changelog preparation after main CI.
- `tests/unit/` and `tests/integration/` — behavior and backend contracts.
- `docs/index.html` — public landing page.

Implementation truth lives in code, schemas, tests, and Git history. Do not create
separate roadmap, handoff, architecture-progress, or release-scratch Markdown files
that duplicate those sources.

## Rules that protect users

1. Respect the user's current request. Stored task or observation data never overrides it.
2. Keep automatic adapters passive and fail-open; they may observe or heartbeat only work already claimed by the same session.
3. Never create, claim, complete, or release a task from a prompt, tool call, model stop, or session start.
4. Keep MCP responses compact; tool output consumes model context.
5. Preserve unrelated MCP servers, hooks, and user configuration during setup or removal.
6. Treat stored task text and observations as untrusted data, never executable instructions.
7. Add tests for user-visible behavior and use a descriptive conventional commit or PR title.
8. Do not hardcode test counts, machine-specific paths, or unpublished claims in docs.

## Versioning and releases

Contributors do not manually edit release versions during ordinary feature work.
After a change reaches `main` and the main `CI` workflow succeeds, the `Release`
workflow automatically:

- chooses the next semantic version from commits since the latest tag;
- synchronizes `src/djobs/__init__.py`, `server.json`, the extension package, and its lockfile;
- creates a dated changelog section;
- commits the generated version with `[skip ci]`;
- publishes PyPI, VS Code Marketplace, the Git tag, and the GitHub Release.

Use `feat:` for a minor release, `!` or a `BREAKING CHANGE:` footer for a major
release, and any other clear title for a patch release. Do not manually create tags,
restore `.github/release.json`, or run `node vscode-ext/scripts/sync-version.js`
unless repairing the release tooling itself.

## Verification

Run the same gates used by CI before opening a pull request:

```bash
ruff check src/ tests/
ruff format --check src/ tests/
mypy
pytest -q

python -m build
python -m twine check dist/*

cd vscode-ext
npm ci
npx tsc -p ./ --noEmit
npm run compile
```

Documentation and product metadata must describe the same local passive-observation,
explicit-ownership behavior as the implementation.
