# Package the 0.8.0 unreleased changes (vs online origin/main @ v0.7.3) into a Desktop zip.
$ErrorActionPreference = 'Stop'
$repo = 'C:\src\my\djobs'
Set-Location $repo

$desktop = [Environment]::GetFolderPath('Desktop')
$ver = '0.8.0'
$stage = Join-Path $desktop "djobs_${ver}_changes"
$filesDir = Join-Path $stage 'files'
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Path $filesDir -Force | Out-Null

# Stage everything so new files are seen; .gitignore auto-excludes local-only files.
git add -A | Out-Null

# Files that differ from origin/main, minus the machine-specific mcp.json.
$changed = git diff --cached origin/main --name-only |
    Where-Object { $_ -and $_ -ne '.vscode/mcp.json' }

foreach ($f in $changed) {
    $src = Join-Path $repo $f
    if (Test-Path $src) {
        $dest = Join-Path $filesDir $f
        New-Item -ItemType Directory -Path (Split-Path $dest -Parent) -Force | Out-Null
        Copy-Item $src $dest -Force
    }
}

# Full unified diff vs online (excluding mcp.json).
$patch = Join-Path $stage "changes-vs-online-v0.7.3.patch"
git diff --cached origin/main -- . ':(exclude).vscode/mcp.json' |
    Out-File -FilePath $patch -Encoding utf8

$nameStatus = git diff --cached origin/main --name-status |
    Where-Object { $_ -notmatch '\.vscode/mcp\.json$' }

$manifest = Join-Path $stage 'MANIFEST.txt'
$lines = @()
$lines += "djobs $ver - changes vs online"
$lines += "Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
$lines += ''
$lines += 'Baseline (online): origin/main @ v0.7.3  https://github.com/jhuang-tw/djobs'
$lines += "This release: $ver (minor bump; backward-compatible features)."
$lines += ''
$lines += 'Excluded (machine-specific / local-only, all gitignored): .vscode/mcp.json,'
$lines += '  SYNC_LIST.local.md, .github/copilot-instructions.md, .github/skills/*/evals/.'
$lines += ''
$lines += 'Changed files (A=added, M=modified) vs online:'
$lines += $nameStatus
$lines += ''
$lines += 'Apply on the release machine C:\dev\djobs by copying files/ over the repo,'
$lines += 'or review changes-vs-online-v0.7.3.patch. Then follow the release cmds.'
$lines | Out-File -FilePath $manifest -Encoding utf8

# Restore the index (never commit on this machine).
git reset --quiet | Out-Null

$zip = Join-Path $desktop "djobs_${ver}_changes.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -Force
Remove-Item $stage -Recurse -Force

$count = ($changed | Measure-Object).Count
Write-Output "ZIP: $zip"
Write-Output "Files packaged: $count"
Write-Output ('Zip size (KB): {0:N1}' -f ((Get-Item $zip).Length / 1KB))
