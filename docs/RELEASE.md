# Release runbook

This is the repeatable release process for djobs. Use the release machine
`C:\dev\djobs` for commits, pushes, tags, PyPI, and Marketplace publishing.
The work machine `c:\src\my\djobs` is edit-only.

## Release contract

- Version lives in one place: `src/djobs/__init__.py` `__version__`.
- The VS Code extension and `server.json` versions are synced by
  `node vscode-ext/scripts/sync-version.js`.
- `CHANGELOG.md` is the release-note source of truth. The GitHub Release
  workflow copies the `## [x.y.z] - YYYY-MM-DD` section into the release body.
- Push exactly one tag: `git push origin vX.Y.Z`. Never use `git push --tags`.
- Never reuse a published version. PyPI and the Marketplace reject duplicates.

## 1. Prepare changes on the work machine

On `c:\src\my\djobs`:

```powershell
$env:PYTHONPATH = "$PWD/src"
.\.venv\Scripts\python.exe -m ruff check src/ tests/
.\.venv\Scripts\python.exe -m ruff format --check src/ tests/
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m pytest -q
Push-Location vscode-ext; npx tsc -p ./ --noEmit; npm run compile; Pop-Location
```

Update `CHANGELOG.md` before release:

1. Move user-facing entries from `[Unreleased]` into `## [X.Y.Z] - YYYY-MM-DD`.
2. Leave a fresh empty `[Unreleased]` section at the top.
3. Keep the release section detailed enough to be copied directly to GitHub
   Releases.

If syncing to the release machine by zip, include only repo files. Exclude
machine-local files such as `.vscode/mcp.json`, `.vscode/settings.json`,
`.github/copilot-instructions.md`, and unfinished local prototypes.

## 2. Apply and verify on the release machine

On `C:\dev\djobs` after applying the changes:

```powershell
Set-Location C:\dev\djobs
git status --short
git diff --stat
```

If the virtual environment behaves strangely, rebuild it instead of debugging
broken binary wheels:

```powershell
deactivate 2>$null
Remove-Item .venv -Recurse -Force
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv\Scripts\python.exe -m pip cache purge
.\.venv\Scripts\python.exe -m pip install --no-cache-dir -e ".[dev,pg]"
.\.venv\Scripts\python.exe -m pip check
```

Run the gates:

```powershell
$env:PYTHONPATH = "$PWD/src"
.\.venv\Scripts\python.exe -m ruff check src/ tests/
.\.venv\Scripts\python.exe -m ruff format --check src/ tests/
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m pytest -q
Push-Location vscode-ext; npx tsc -p ./ --noEmit; npm run compile; Pop-Location
```

## 3. Pre-tag checks

Replace `X.Y.Z` with the release version:

```powershell
$version = "X.Y.Z"
Select-String -Path src\djobs\__init__.py -Pattern "__version__"
Select-String -Path vscode-ext\package.json -Pattern '"version"'
Select-String -Path server.json -Pattern '"version"'
git tag --list "v$version"
```

`git tag --list "v$version"` must print nothing. If it prints a tag, stop and
choose a new version.

Confirm the release notes section exists:

```powershell
Select-String -Path CHANGELOG.md -Pattern "## \[$version\] -"
```

## 4. Commit and push main

```powershell
git add -A
git status --short
git diff --cached --stat
git commit -m "Release djobs X.Y.Z"
git push origin main
```

Wait for the main-branch CI run to pass before tagging.

## 5. Tag exactly the release commit

```powershell
git log --oneline --decorate -3
git tag "v$version" HEAD
git push origin "v$version"
```

Do not use `git push --tags`.

Verify the tag points to the same commit locally and remotely:

```powershell
git rev-parse HEAD
git rev-parse "v$version"
git ls-remote --tags origin "v$version"
```

All hashes must match.

If a tag was created on the wrong commit and has not been consumed yet:

```powershell
git tag -d "v$version"
git push origin ":refs/tags/v$version"
git tag "v$version" HEAD
git push origin "v$version"
```

## 6. Watch release automation

The tag triggers `.github/workflows/publish.yml`:

- GitHub Release body is copied from `CHANGELOG.md`.
- PyPI package is built, checked, and published.
- VS Code extension is compiled and published.
- If the Marketplace version already exists, the workflow treats that duplicate
  publish as success. It is still better to avoid rerunning duplicate tags.

Useful checks:

```powershell
git ls-remote --tags origin "v$version"
```

If GitHub CLI is installed:

```powershell
gh run list --workflow publish.yml --limit 5
gh release view "v$version" --web
```

Without GitHub CLI, use the GitHub Actions and Releases web pages.

## 7. GitHub Pages

GitHub Pages is not a package release blocker. It requires repository settings:

1. GitHub repository Settings -> Pages.
2. Build and deployment -> Source -> GitHub Actions.
3. Run the `GitHub Pages` workflow again.

If GitHub CLI is installed:

```powershell
gh workflow run pages.yml
```

## 8. After release

Confirm public surfaces:

- GitHub Release has detailed notes from `CHANGELOG.md`.
- PyPI shows the new version.
- VS Code Marketplace shows the new extension version.
- The MCP Registry manifest version matches the package version.
- Pages deploy is green, if Pages is enabled.
