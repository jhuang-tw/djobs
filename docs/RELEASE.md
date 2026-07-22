# Release runbook

This is the canonical publishing process for djobs. The Python package, VS Code
extension, and MCP Registry manifest use one release version.

## 1. Choose the version

Update `src/djobs/__init__.py`, then synchronize manifests:

```bash
node vscode-ext/scripts/sync-version.js
```

Confirm the version is new and appears in:

- `src/djobs/__init__.py`
- `server.json` and its package entry
- `vscode-ext/package.json`
- `vscode-ext/package-lock.json`

Never reuse a published version.

## 2. Prepare release notes

Move user-facing entries from `[Unreleased]` into a dated section:

```text
## [X.Y.Z] - YYYY-MM-DD
```

Leave a fresh `[Unreleased]` heading above it. `CHANGELOG.md` is copied directly
into the GitHub Release body, so it must explain the release without relying on
commit messages.

## 3. Run the full gate

```bash
ruff check src/ tests/
ruff format --check src/ tests/
mypy
pytest -q
cd vscode-ext
npx tsc -p ./ --noEmit
npm run compile
```

Open and merge a pull request, then wait for main-branch CI to pass.

## 4. Publish exactly one commit

The preferred path is to update `.github/release.json` on `main` with the new
version and the exact release commit SHA. The `Release` workflow validates every
manifest, creates the tag and GitHub Release, publishes to PyPI, and publishes the
VS Code extension.

A manual tag is also supported:

```bash
git tag vX.Y.Z <release-commit-sha>
git push origin vX.Y.Z
```

Never use `git push --tags`; publish only the intended tag.

## 5. Verify public surfaces

Confirm:

- the GitHub Release body matches the changelog section;
- PyPI shows the new package version;
- the VS Code Marketplace shows the matching extension version;
- `server.json` matches the release;
- GitHub Pages deployed successfully.

Do not edit already-published artifacts under the same version. Make another
versioned release for follow-up fixes.
