# Release runbook

This is the canonical publishing process for djobs. The Python package, VS Code
extension, MCP Registry manifest, package lock, GitHub Release, and immutable release
tag use one lockstep version.

## Automatic release path

A normal change requires only one pull request:

1. merge the pull request into `main`;
2. the `Release` workflow waits for that exact main commit's `CI` run to pass;
3. it computes the next semantic version and creates a deterministic release snapshot;
4. it publishes PyPI and the VS Code Marketplace, creates the immutable tag and
   GitHub Release, then removes its temporary branch.

No follow-up edit to `.github/release.json`, manual version bump, tag, or separate
PyPI command is required.

## Version selection

`scripts/prepare_auto_release.py` examines commits since the latest `vX.Y.Z` tag:

- a conventional `feat:` commit selects a minor release;
- a `!` marker or `BREAKING CHANGE:` footer selects a major release;
- every other merged change selects a patch release.

The highest required bump wins. The latest immutable release tag is the version
authority, so releases continue from the published version even when protected
`main` intentionally retains an older checked-in version.

## Protected-main release snapshot

The repository rules require every `main` change to use a pull request and pass all
required checks. GitHub Actions is therefore not allowed to push a generated version
commit directly to `main`.

After main CI succeeds, the workflow updates these files in a temporary
`automation/release-vX.Y.Z` snapshot:

- `src/djobs/__init__.py`;
- `server.json` and every package entry;
- `vscode-ext/package.json`;
- `vscode-ext/package-lock.json` and its root package entry;
- `CHANGELOG.md` with a dated release section.

The snapshot commit uses a deterministic date from the source main commit. A retry
therefore recreates the same release commit instead of inventing another version.
The temporary branch keeps the commit reachable while publishing and is deleted only
after the GitHub Release succeeds. The immutable tag permanently retains the exact
published source.

## Publishing order and retries

The workflow publishes in this order:

1. build and publish the Python package to PyPI with trusted publishing;
2. compile and publish the VS Code extension;
3. create or update the immutable Git tag and GitHub Release;
4. delete the temporary release branch.

PyPI and Marketplace duplicate versions are treated idempotently. If publication is
interrupted, re-run the `Release` workflow. It derives the same version from the
latest tag and source history, force-updates only the dedicated temporary branch, and
resumes without writing to protected `main`.

## Manual recovery only

`workflow_dispatch` remains available for retrying an interrupted publication. It is
not a second versioning procedure and asks for neither a version nor a target SHA.
Never manually upload a package or create an independent tag with mismatched content.

## Verification

Confirm that:

- the GitHub Release body matches the tagged changelog section;
- PyPI shows the same package version;
- the VS Code Marketplace shows the same extension version;
- `server.json`, `src/djobs/__init__.py`, and extension manifests match inside the tag;
- no `automation/release-vX.Y.Z` branch remains after a successful release.

Do not edit already-published artifacts under the same version. Merge another normal
change and let the automatic workflow derive the next release from the latest tag.
