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
python -m pip install -e ".[dev,pg]"
pre-commit install
```

The project supports Python 3.10 through 3.14. PostgreSQL is optional at runtime,
but the `pg` extra keeps type checking and repository-contract tests complete.
Ruff is pinned in the development extra so local formatting and CI cannot drift.

## Repository map

- `src/djobs/workspace.py` — local repository and cross-shell identity resolution.
- `src/djobs/handoff.py` — compact workspace reads plus explicit checkpoint and handoff.
- `src/djobs/observations.py` and `lifecycle.py` — passive, bounded local observations.
- `src/djobs/host_hooks.py` and `setup_cli.py` — host-specific local adapters and safe setup.
- `src/djobs/coding_mcp.py` — the default compact coding MCP surface.
- `src/djobs/auto_hook.py` — legacy explicit command-checkpoint compatibility only; normal setup must not install it.
- `src/djobs/core/`, `queue/`, `storage/`, `worker/` — durable queue internals and optional general job execution.
- `vscode-ext/` — headless native MCP registration, passive adapter setup, and diagnostics.
- `scripts/preflight.py` — the shared local and CI validation entry point.
- `scripts/prepare_auto_release.py` — deterministic tag-backed SemVer, manifest, and changelog preparation after main CI.
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

## One preflight command

Before pushing, run:

```bash
python scripts/preflight.py --profile quick --fix --base-ref origin/main
```

The installed pre-push hook runs the same command. It formats with the pinned Ruff
version first. If formatting changes a file, the command stops so you can review and
stage that change before rerunning. It then performs lint, type checking, change-aware
tests, and an extension build only when extension implementation inputs changed.

For a release-grade local pass, including package metadata and the extension build:

```bash
python scripts/preflight.py --profile full --check --base-ref origin/main
```

GitHub CI invokes the same script in non-mutating `--check` mode. Do not maintain a
second hand-written list of local checks.

## CI layers

Ordinary pull requests run one fast required `lint` check containing the complete
quick preflight. The legacy required check names remain present but their expensive
jobs are skipped for ordinary PRs.

The full compatibility matrix runs for:

- every commit that reaches `main`;
- `workflow_dispatch` verification;
- automated `automation/release-vX.Y.Z` pull requests.

Full CI includes Python 3.10–3.14, PostgreSQL, package and Twine validation, VS Code
compilation, and clean-wheel installation on Windows, macOS, and Linux. New pushes to
the same PR cancel obsolete runs instead of waiting for every stale commit.

## Versioning and releases

Contributors do not manually edit release versions during ordinary feature work.
After a change reaches protected `main` and its full `CI` workflow succeeds, the
`Release` workflow automatically:

- chooses the next semantic version from commits since the latest immutable tag;
- opens an `automation/release-vX.Y.Z` PR with synchronized Python, MCP, extension,
  lockfile, and changelog versions;
- runs the full compatibility matrix once on that exact release commit;
- squash-merges the release PR after every required check passes;
- publishes PyPI and the VS Code Marketplace from the merged commit;
- creates the matching Git tag and GitHub Release.

The release commit changes only generated version surfaces already validated in the
release PR, so it is not followed by a duplicate full main matrix.

Use `feat:` for a minor release, `!` or a `BREAKING CHANGE:` footer for a major
release, and any other clear title for a patch release. After a successful release,
protected `main`, the immutable tag, PyPI, and the Marketplace all carry the same
version. Do not manually create tags, restore `.github/release.json`, or run
`node vscode-ext/scripts/sync-version.js` unless repairing the release tooling itself.

Documentation and product metadata must describe the same local passive-observation,
explicit-ownership behavior as the implementation.
