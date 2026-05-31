<#
.SYNOPSIS
    One-command pre-push preflight. Mirrors the GitHub Actions CI gates so you
    catch failures locally before pushing.

.DESCRIPTION
    Runs, in order:
      1. ruff format   (auto-formats src/ and tests/ in place)
      2. ruff check    (lint)
      3. mypy          (static type check)
      4. pytest        (test suite)
      5. extension compile (vscode-ext, if present)

    Stops at the first failing step with a non-zero exit code.

.PARAMETER SkipTests
    Skip pytest (fast lint/type-only pass).

.PARAMETER SkipExtension
    Skip the VS Code extension compile.

.EXAMPLE
    ./scripts/preflight.ps1
    ./scripts/preflight.ps1 -SkipTests
#>
[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$SkipExtension
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

# Resolve the interpreter once. Prefer the repo's .venv so checks run under the
# same interpreter regardless of whether the venv is activated or PATH drifts.
# Bare ruff/mypy/pytest can resolve to a different (or broken) install.
$venvPython = Join-Path $repoRoot '.venv/Scripts/python.exe'
$python = if (Test-Path $venvPython) { $venvPython } else { 'python' }
Write-Host "Using interpreter: $python" -ForegroundColor DarkGray

function Invoke-Step {
    param([string]$Name, [scriptblock]$Action)
    Write-Host "`n=== $Name ===" -ForegroundColor Cyan
    & $Action
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FAIL] $Name (exit $LASTEXITCODE)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
    Write-Host "[OK] $Name" -ForegroundColor Green
}

Invoke-Step 'ruff format'  { & $python -m ruff format src/ tests/ }
Invoke-Step 'ruff check'   { & $python -m ruff check src/ tests/ }
Invoke-Step 'mypy'         { & $python -m mypy }

if (-not $SkipTests) {
    Invoke-Step 'pytest' { & $python -m pytest -q --tb=short }
}

if (-not $SkipExtension -and (Test-Path (Join-Path $repoRoot 'vscode-ext/package.json'))) {
    Invoke-Step 'extension compile' {
        Push-Location (Join-Path $repoRoot 'vscode-ext')
        try { npm run compile } finally { Pop-Location }
    }
}

Write-Host "`nAll preflight checks passed. Safe to push." -ForegroundColor Green
