<#
.SYNOPSIS
    One-stop release preparation for djobs (work machine: c:\src\my\djobs).

.DESCRIPTION
    Single source of truth for the version is src/djobs/__init__.py (__version__).
    pyproject.toml reads it dynamically (hatchling); the VS Code extension syncs
    it via vscode-ext/scripts/sync-version.js. This script:
      1. (optional) bumps __version__ in src/djobs/__init__.py
      2. runs the test suite
      3. builds the Python sdist/wheel (dist/)
      4. compiles + packages the VS Code extension (.vsix)
      5. prints the remaining manual upload steps

.PARAMETER Version
    New semantic version, e.g. 0.6.1. If omitted, the current version is used.

.PARAMETER SkipTests
    Skip running pytest (not recommended).

.EXAMPLE
    .\scripts\release.ps1 -Version 0.6.1
#>
param(
    [string]$Version,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$initPath = Join-Path $repo "src\djobs\__init__.py"
$python = Join-Path $repo ".venv\Scripts\python.exe"

function Get-CurrentVersion {
    $line = Select-String -Path $initPath -Pattern '__version__\s*=\s*"([^"]+)"'
    if (-not $line) { throw "Could not find __version__ in $initPath" }
    return $line.Matches[0].Groups[1].Value
}

# 1. Optionally bump the single source of truth.
if ($Version) {
    if ($Version -notmatch '^\d+\.\d+\.\d+') {
        throw "Version '$Version' is not semantic (expected like 0.6.1)."
    }
    $content = Get-Content $initPath -Raw
    $content = [regex]::Replace($content, '__version__\s*=\s*"[^"]+"', "__version__ = `"$Version`"")
    Set-Content -Path $initPath -Value $content -NoNewline
    Write-Host "Bumped __version__ -> $Version" -ForegroundColor Green
}

$ver = Get-CurrentVersion
Write-Host "Releasing djobs $ver" -ForegroundColor Cyan

# 2. Tests.
if (-not $SkipTests) {
    Write-Host "Running tests..." -ForegroundColor Cyan
    $env:PYTHONPATH = Join-Path $repo "src"
    & $python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Tests failed; aborting release." }
}

# 3. Build Python package.
Write-Host "Building Python sdist/wheel..." -ForegroundColor Cyan
Push-Location $repo
try {
    if (Test-Path "dist") { Remove-Item "dist" -Recurse -Force }
    & $python -m pip install --upgrade build -q
    & $python -m build
    if ($LASTEXITCODE -ne 0) { throw "python -m build failed." }
} finally {
    Pop-Location
}

# 4. Build the VS Code extension.
Write-Host "Packaging VS Code extension..." -ForegroundColor Cyan
Push-Location (Join-Path $repo "vscode-ext")
try {
    Get-ChildItem -Filter "djobs-*.vsix" | Remove-Item -Force -ErrorAction SilentlyContinue
    npm run compile
    if ($LASTEXITCODE -ne 0) { throw "Extension compile failed." }
    npx @vscode/vsce package
    if ($LASTEXITCODE -ne 0) { throw "vsce package failed." }
} finally {
    Pop-Location
}

$vsix = Join-Path $repo "vscode-ext\djobs-$ver.vsix"

# 5. Next steps.
Write-Host ""
Write-Host "=== djobs $ver prepared ===" -ForegroundColor Green
Write-Host "Python dist:  $(Join-Path $repo 'dist')"
Write-Host "Extension:    $vsix"
Write-Host ""
Write-Host "Remaining manual steps:" -ForegroundColor Yellow
Write-Host "  1. Commit + push (from C:\dev\djobs):  git tag v$ver; git push --tags"
Write-Host "  2. Create a GitHub Release for v$ver  -> triggers publish.yml -> PyPI (automatic)"
Write-Host "  3. Marketplace: manage page -> Update -> upload djobs-$ver.vsix"
