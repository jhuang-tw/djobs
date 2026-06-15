"""Read-only git working-tree inspection.

The "AI Work Receipt" is more trustworthy when it shows what git *actually*
sees changed, not only what the agent claimed in task evidence. This module
captures that ground truth with a couple of read-only git commands.

It is deliberately defensive and dependency-free:

- runs ``git`` via ``subprocess`` with an argument list (never a shell), so
  there is no shell-injection surface;
- has a short timeout so a hung git can never wedge a receipt;
- returns a structured result (never raises) so callers can fold it into a
  report whether or not the directory is a git repo or git is installed.

It only *reads* (``rev-parse``, ``status``, ``diff --shortstat``). It never
stages, commits, or mutates anything.
"""

from __future__ import annotations

import subprocess
from typing import Any

_GIT_TIMEOUT_SECONDS = 5


def _run_git(cwd: str, args: list[str]) -> tuple[bool, str]:
    """Run ``git <args>`` in *cwd*. Returns (ok, stdout-or-error)."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (FileNotFoundError, OSError):
        return False, "git not available"
    except subprocess.TimeoutExpired:
        return False, "git timed out"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "git error").strip()
    return True, proc.stdout


def _parse_porcelain(output: str) -> list[str]:
    """Parse ``git status --porcelain`` into a sorted list of changed paths."""
    files: set[str] = set()
    for raw in output.splitlines():
        if not raw.strip():
            continue
        # Format: "XY <path>" or, for renames, "XY <old> -> <new>".
        path = raw[3:] if len(raw) > 3 else raw.strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip().strip('"')
        if path:
            files.add(path)
    return sorted(files)


def working_tree_changes(cwd: str) -> dict[str, Any]:
    """Return a read-only snapshot of the git working tree at *cwd*.

    Always returns a dict. When *cwd* is not a git repo (or git is missing), it
    reports ``is_git_repo: False`` with a human ``reason`` and no file list, so
    the receipt can simply omit the git section without special-casing errors.
    """
    ok, inside_work_tree = _run_git(cwd, ["rev-parse", "--is-inside-work-tree"])
    if not ok or inside_work_tree.strip() != "true":
        return {"is_git_repo": False, "reason": "not a git repository (or git unavailable)"}

    status_ok, status_out = _run_git(cwd, ["status", "--porcelain"])
    if not status_ok:
        return {"is_git_repo": True, "reason": status_out}

    changed = _parse_porcelain(status_out)

    diff_summary: str | None = None
    # ``--shortstat HEAD`` needs at least one commit; ignore failure on a fresh repo.
    diff_ok, diff_out = _run_git(cwd, ["diff", "--shortstat", "HEAD"])
    if diff_ok and diff_out.strip():
        diff_summary = diff_out.strip()

    return {
        "is_git_repo": True,
        "changed_file_count": len(changed),
        "changed_files": changed,
        "diff_summary": diff_summary,
    }
