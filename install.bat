@echo off
REM ============================================================================
REM djobs — one-shot developer setup for Windows.
REM
REM Double-click this file (or run `install.bat` from a terminal) to:
REM   1. create a local virtual environment (.venv) if missing
REM   2. install djobs + dev tools + Postgres extra (ruff, mypy, pytest,
REM      pre-commit, psycopg)
REM   3. wire up the pre-push gate so `git push` runs the same checks as CI
REM      locally first (no more waiting for a red CI run)
REM
REM Safe to re-run anytime; it only does what's missing.
REM ============================================================================
setlocal EnableExtensions

cd /d "%~dp0"

echo.
echo === djobs setup =============================================
echo.

REM --- 1. locate a Python interpreter -----------------------------------------
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
    where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo [ERROR] Python was not found on PATH.
    echo         Install Python 3.11+ from https://www.python.org/downloads/
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

:fail
echo.
echo [ERROR] setup failed — see the messages above.
exit /b 1
