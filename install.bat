@echo off
REM ============================================================================
REM djobs — setup for Windows. Two paths:
REM
REM   install.bat            CONTRIBUTOR setup (default): create a local .venv,
REM                          install djobs + dev tools + Postgres extra, and
REM                          wire the pre-push gate. Use this if you cloned the
REM                          repo to work ON djobs.
REM
REM   install.bat user       USER setup: install djobs as a global tool (pipx,
REM                          or pip --user fallback) so any project can use it.
REM                          No .venv, no dev tools. Use this if you just want
REM                          crash-proof task memory for your AI agent.
REM
REM Both are safe to re-run anytime; they only do what's missing.
REM ============================================================================
setlocal EnableExtensions

cd /d "%~dp0"

if /i "%~1"=="user" goto :user_setup
if /i "%~1"=="--user" goto :user_setup

echo.
echo === djobs setup (contributor) ==============================
echo.
echo   Just want to USE djobs, not develop it?  Run:  install.bat user
echo   (or simply:  pipx install djobs)
echo.

REM --- 1. locate a Python interpreter -----------------------------------------
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
    where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo [ERROR] Python was not found on PATH.
    echo         Install Python 3.10+ from https://www.python.org/downloads/
    echo         and re-run this script.
    goto :fail
)

REM --- 2. create the virtual environment --------------------------------------
if exist ".venv\Scripts\python.exe" (
    echo [OK]   .venv already exists
) else (
    echo [..]   creating virtual environment in .venv
    %PY% -m venv .venv
    if errorlevel 1 goto :fail
    echo [OK]   .venv created
)

set "VENV_PY=.venv\Scripts\python.exe"

REM --- 3. install dependencies ------------------------------------------------
REM Use --no-cache-dir to sidestep a corrupted/legacy pip http cache, which on
REM some machines spams "Cache entry deserialization failed, entry ignored".
REM Progress output is left ON (no --quiet) so a slow download never looks hung.
echo [..]   upgrading pip, setuptools, wheel
"%VENV_PY%" -m pip install --no-cache-dir --upgrade pip setuptools wheel
if errorlevel 1 goto :fail

echo.
echo [..]   installing djobs with dev + pg extras
echo        (downloading packages - this can take a minute, progress shown below)
echo.
"%VENV_PY%" -m pip install --no-cache-dir -e ".[dev,pg]"
if errorlevel 1 goto :fail
echo [OK]   dependencies installed

REM --- 4. enable the local pre-push gate --------------------------------------
REM If an older fallback shell hook was wired via core.hooksPath, pre-commit
REM refuses to install. Clear it first (ignore errors if it was never set).
git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo [WARN] not a git repository - skipping pre-push hook setup
) else (
    git config --unset core.hooksPath >nul 2>&1
    echo [..]   installing pre-push hook via pre-commit
    "%VENV_PY%" -m pre_commit install
    if errorlevel 1 goto :fail
    echo [OK]   pre-push gate active - 'git push' now runs ruff + mypy + pytest first
)

echo.
echo === setup complete =========================================
echo.
echo Next steps:
echo   - Activate the environment:  .venv\Scripts\activate
echo   - Run all checks manually:    pre-commit run --all-files
echo   - Push as usual; the gate runs automatically before each push.
echo   - Emergency bypass (rare):    git push --no-verify
echo.
goto :eof

:user_setup
echo.
echo === djobs setup (user / global tool) =======================
echo.
where pipx >nul 2>&1
if not errorlevel 1 (
    echo [..]   installing djobs globally via pipx
    pipx install djobs
    if errorlevel 1 goto :fail
    echo [OK]   djobs installed (pipx). Run 'djobs install-mcp' in any project.
    goto :user_done
)
echo [WARN] pipx not found - falling back to a user-level pip install.
echo        For an isolated install instead, run:  python -m pip install --user pipx
echo.
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
    where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo [ERROR] Python was not found on PATH.
    echo         Install Python 3.10+ from https://www.python.org/downloads/
    echo         and re-run this script.
    goto :fail
)
echo [..]   installing djobs via pip --user
%PY% -m pip install --user --upgrade djobs
if errorlevel 1 goto :fail
echo [OK]   djobs installed (pip --user). Run 'djobs install-mcp' in any project.

:user_done
echo.
echo === setup complete =========================================
echo.
echo Next steps:
echo   - Wire a project:   cd your-project ^&^& djobs install-mcp
echo   - Check the setup:  djobs doctor
echo.
goto :eof

:fail
echo.
echo [ERROR] setup failed — see the messages above.
exit /b 1
