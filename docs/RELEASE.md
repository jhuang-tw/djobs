# Release runbook

This is the canonical publishing process for djobs. The Python package, VS Code
extension, MCP Registry manifest, package lock, GitHub Release, and public artifacts
use one lockstep version.

## Automatic release path

A normal change now requires only one pull request:

1. merge the pull request into `main`;
2. wait for the main-branch `CI` workflow to pass;
3. the `Release` workflow computes the next semantic version, synchronizes all
   published manifests, creates a release commit, and publishes every surface.

No follow-up edit to `.github/release.json`, manual tag, or separate PyPI command is
required.

## Version selection

`scripts/prepare_auto_release.py` examines commits since the latest `vX.Y.Z` tag:

- a conventional `feat:` commit selects a minor release;
- a `!` marker or `BREAKING CHANGE:` footer selects a major release;
- every other merged change selects a patch release.

The highest required bump wins when several commits are released together. Because
all merges are versioned, non-conventional commit titles still produce a patch
release rather than silently skipping publication.

## Generated release commit

After CI succeeds, GitHub Actions updates:

- `src/djobs/__init__.py`;
- `server.json` and every package entry;
- `vscode-ext/package.json`;
- `vscode-ext/package-lock.json` and its root package entry;
- `CHANGELOG.md` with a dated release section.

It commits those changes as:

```text
chore(release): vX.Y.Z [skip ci]
```

`[skip ci]` prevents the generated version commit from recursively creating another
release. The workflow uses a concurrency lock so closely spaced merges are folded
into one ordered release instead of racing.

## Publishing order and retries

The workflow publishes in this order:

1. build and publish the Python package to PyPI with trusted publishing;
2. compile and publish the VS Code extension;
3. create the immutable Git tag and GitHub Release from the matching changelog section.

PyPI and Marketplace duplicate versions are treated idempotently. If publication is
interrupted after the release commit reaches `main`, manually re-run the `Release`
workflow; it detects the untagged prepared version and resumes instead of incrementing
again.

## Required repository setting

The Release workflow must be allowed to write its generated `chore(release)` commit
to `main`. When a repository ruleset blocks every direct write, add GitHub Actions as
a bypass actor for this single generated release path. Normal contributor changes
continue to require pull requests and CI.

## Manual recovery only

`workflow_dispatch` remains available for retrying an interrupted publication. It is
not a second versioning procedure and does not ask for a version number or target SHA.
The workflow always derives both from repository history.

Never create an independent manual tag or upload a package under a version that is
not represented by the generated release commit.

## Verification

Confirm that:

- the GitHub Release body matches the dated changelog section;
- PyPI shows the same package version;
- the VS Code Marketplace shows the same extension version;
- `server.json` and `src/djobs/__init__.py` match the release tag;
- GitHub Pages deploys successfully.

Do not edit already-published artifacts under the same version. Merge another change
and let the automatic workflow create the next semantic version.
