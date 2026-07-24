# Release runbook

This is the canonical publishing process for djobs. The Python package, VS Code
extension, MCP Registry manifest, package lock, protected `main`, GitHub Release,
and immutable release tag use one lockstep version.

## Automatic release path

A normal change requires only one human pull request:

1. the human PR runs the quick shared preflight;
2. after merge, `main` runs the full compatibility matrix;
3. the `Release` workflow selects that validated main commit;
4. it computes the next semantic version and opens an automated
   `automation/release-vX.Y.Z` pull request;
5. that exact release commit runs the full compatibility matrix once;
6. after all required checks pass, the workflow squash-merges the release PR;
7. it publishes PyPI and the VS Code Marketplace, then creates the matching immutable
   tag and GitHub Release.

No follow-up edit to `.github/release.json`, manual version bump, tag, merge, or
separate PyPI command is required.

## Version selection

`scripts/prepare_auto_release.py` examines commits since the latest `vX.Y.Z` tag:

- a conventional `feat:` commit selects a minor release;
- a `!` marker or `BREAKING CHANGE:` footer selects a major release;
- every other merged change selects a patch release.

The highest required bump wins. The latest immutable release tag is used as the
starting version, while the automatically merged release PR makes protected `main`
match the new published version before any registry upload begins.

## Shared preflight and CI layers

`scripts/preflight.py` is the single local and CI validation entry point. Developers
run the mutating quick form before pushing:

```bash
python scripts/preflight.py --profile quick --fix --base-ref origin/main
```

CI uses the non-mutating `--check` form. Ordinary human PRs run one quick required
preflight. The full matrix is reserved for `main`, manual verification, and automated
release PRs. A newer push cancels an obsolete CI run for the same PR.

Full compatibility validation includes:

- Python 3.10 through 3.14;
- PostgreSQL repository-contract tests;
- package build and Twine metadata validation;
- VS Code TypeScript compilation;
- clean-wheel installation on Windows, macOS, and Linux.

## Protected-main release PR

The repository rules require every `main` change to use a pull request and pass all
required checks. The Release workflow therefore never pushes a generated version
commit directly to `main`.

After source CI succeeds, the workflow updates these files on a deterministic
`automation/release-vX.Y.Z` branch:

- `src/djobs/__init__.py`;
- `server.json` and every package entry;
- `vscode-ext/package.json`;
- `vscode-ext/package-lock.json` and its root package entry;
- `CHANGELOG.md` with a dated release section.

GitHub may gate CI for a pull request created with `GITHUB_TOKEN`. The Release job
finds the exact pull-request CI run, approves it when required, waits for the full
matrix and required checks, then squash-merges the PR.

The release commit uses a deterministic timestamp from the selected main commit. A
retry recreates the same branch commit instead of inventing another version. If `main`
advances while the release PR is validating, the workflow closes that stale PR and
lets the newer queued Release run create one from the newer main state.

## Publishing order and retries

The workflow publishes in this order:

1. validate the exact release commit through its release PR full matrix;
2. merge that checked release PR into `main`;
3. build and publish the Python package to PyPI with trusted publishing;
4. compile and publish the VS Code extension;
5. create or update the immutable Git tag and GitHub Release.

The merged release commit differs from the checked PR only by the squash commit
identity, so the workflow does not dispatch a duplicate full matrix after merging.
The package and extension publishing jobs still rebuild from the exact merged SHA.

PyPI and Marketplace duplicate versions are treated idempotently. If publication is
interrupted after the release PR reaches `main`, re-run the `Release` workflow. The
planner detects the checked-in version that has not yet been tagged and resumes that
same release without another version bump or another PR.

## Required repository setting

Repository **Settings → Actions → General → Workflow permissions** must use:

- **Read and write permissions**;
- **Allow GitHub Actions to create and approve pull requests**.

The main ruleset remains fully active. Do not add GitHub Actions to the bypass list and
do not weaken required status checks.

## Manual recovery only

`workflow_dispatch` remains available for retrying an interrupted publication. It is
not a second versioning procedure and asks for neither a version nor a target SHA.
Never manually upload a package or create an independent tag with mismatched content.

## Verification

Confirm that:

- `main`, the immutable tag, PyPI, and the VS Code Marketplace show the same version;
- the GitHub Release body matches the tagged changelog section;
- the automated release PR was merged through the normal ruleset;
- no `automation/release-vX.Y.Z` branch remains after the squash merge.

Do not edit already-published artifacts under the same version. Merge another normal
change and let the automatic workflow derive the next release from the latest tag.
