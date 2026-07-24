# Release runbook

This is the canonical publishing process for djobs. The Python package, VS Code
extension, MCP Registry manifest, package lock, protected `main`, GitHub Release,
and immutable release tag use one lockstep version.

## Automatic release path

A normal change requires only one human pull request:

1. merge the change into `main`;
2. the `Release` workflow selects a main commit whose `CI` run passed;
3. it computes the next semantic version and opens an automated
   `automation/release-vX.Y.Z` pull request;
4. it explicitly runs the full `CI` workflow on that release commit;
5. after all required checks pass, it squash-merges the release PR into protected
   `main` and waits for the merged commit's main CI;
6. it publishes PyPI and the VS Code Marketplace, then creates the matching immutable
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

GitHub suppresses recursive workflow events created with `GITHUB_TOKEN`, so the
Release workflow explicitly dispatches `CI` for the release branch. Those checks are
attached to the release commit and satisfy the same ruleset used by human pull
requests. No review is required because the ruleset requires checks but specifies zero
approvals.

The release commit uses a deterministic timestamp from the selected main commit. A
retry recreates the same branch commit instead of inventing another version. If `main`
advances while the release PR is validating, the workflow closes that stale PR and
lets the newer queued Release run create one from the newer main state.

## Publishing order and retries

The workflow publishes in this order:

1. merge the checked release PR into `main`;
2. wait for the merged main commit's CI to pass;
3. build and publish the Python package to PyPI with trusted publishing;
4. compile and publish the VS Code extension;
5. create or update the immutable Git tag and GitHub Release.

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
