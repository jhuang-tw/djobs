"""Optional ARUN project-mode bridge.

This module intentionally does not embed or reimplement ARUN.  djobs remains the
local memory/coordination layer; ARUN remains the authoritative durable project
state engine when project mode is explicitly used.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

_DEFAULT_TIMEOUT_SECONDS = 30.0


class ProjectModeError(RuntimeError):
    """Raised when the optional ARUN bridge cannot satisfy a project command."""


def _root(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def arun_executable() -> str | None:
    """Return the configured ARUN executable, if available.

    ``DJOBS_ARUN_COMMAND`` may point at an alternate executable.  It is treated as
    one executable path/name, never as shell syntax.
    """

    command = os.environ.get("DJOBS_ARUN_COMMAND", "arun").strip() or "arun"
    return shutil.which(command)


def _require_arun() -> str:
    executable = arun_executable()
    if executable is None:
        raise ProjectModeError(
            "ARUN is not available on PATH. Install/activate ARUN first, or set "
            "DJOBS_ARUN_COMMAND to its executable path. Normal djobs memory remains available."
        )
    return executable


def _run_arun(
    arguments: Sequence[str],
    *,
    root: str | Path,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Execute one bounded ARUN CLI command without a shell."""

    executable = _require_arun()
    try:
        return subprocess.run(
            [executable, *arguments],
            cwd=_root(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProjectModeError(f"ARUN command timed out after {timeout:g}s") from exc
    except OSError as exc:
        raise ProjectModeError(f"Could not start ARUN: {exc}") from exc


def _json_stdout(result: subprocess.CompletedProcess[str], *, command: str) -> dict[str, object]:
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or f"exit {result.returncode}"
        raise ProjectModeError(f"ARUN {command} failed: {detail}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProjectModeError(f"ARUN {command} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ProjectModeError(f"ARUN {command} returned a non-object payload")
    return payload


def resolve_project_id(root: str | Path) -> str | None:
    """Resolve the ARUN project bound to ``root`` without creating one."""

    canonical_root = _root(root)
    result = _run_arun(
        ["control", "resolve", "--root", str(canonical_root)],
        root=canonical_root,
    )
    payload = _json_stdout(result, command="control resolve")
    selected = payload.get("selected_project_id")
    if selected is None:
        return None
    if not isinstance(selected, str) or not selected.strip():
        raise ProjectModeError("ARUN control resolve returned an invalid project id")
    return selected.strip()


def _emit(result: subprocess.CompletedProcess[str]) -> int:
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)
    return int(result.returncode)


def _cmd_doctor(args: argparse.Namespace) -> int:
    executable = arun_executable()
    payload = {
        "ok": executable is not None,
        "mode": "optional-arun-project-mode",
        "arun_executable": executable,
        "memory_available_without_arun": True,
        "next_step": (
            "Use 'djobs project init --objective ... --acceptance ...' in a repository."
            if executable
            else "Install/activate ARUN or set DJOBS_ARUN_COMMAND."
        ),
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        mark = "OK" if executable else "UNAVAILABLE"
        print(f"djobs project doctor — {mark}")
        print(f"  ARUN: {executable or 'not found'}")
        print("  djobs memory: available independently")
        print(f"  Next: {payload['next_step']}")
    return 0 if executable else 1


def _cmd_init(args: argparse.Namespace) -> int:
    canonical_root = _root(args.root)
    existing = resolve_project_id(canonical_root)
    if existing is not None:
        raise ProjectModeError(
            f"ARUN project {existing} already owns this root; use 'djobs project status' instead."
        )

    command = [
        "create",
        "--root",
        str(canonical_root),
        "--objective",
        args.objective,
    ]
    for constraint in args.constraint:
        command.extend(["--constraint", constraint])
    for acceptance in args.acceptance:
        command.extend(["--acceptance", acceptance])
    return _emit(_run_arun(command, root=canonical_root))


def _project_id(args: argparse.Namespace) -> tuple[Path, str]:
    canonical_root = _root(args.root)
    project_id = args.project_id or resolve_project_id(canonical_root)
    if project_id is None:
        raise ProjectModeError(
            "No ARUN project is bound to this repository. Run 'djobs project init' first."
        )
    return canonical_root, project_id


def _cmd_status(args: argparse.Namespace) -> int:
    canonical_root, project_id = _project_id(args)
    return _emit(
        _run_arun(
            ["control", "status", project_id],
            root=canonical_root,
        )
    )


def _cmd_next(args: argparse.Namespace) -> int:
    canonical_root, project_id = _project_id(args)
    return _emit(
        _run_arun(
            ["control", "next", project_id],
            root=canonical_root,
        )
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="djobs project",
        description=(
            "Optional ARUN project mode: keep djobs memory/coordination, and add durable "
            "goal, recovery, scope, evidence, and verified-completion state when ARUN is installed."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser("doctor", help="Check whether optional ARUN project mode is ready")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=_cmd_doctor)

    init = commands.add_parser("init", help="Create one ARUN project for this repository")
    init.add_argument("--root", default=".")
    init.add_argument("--objective", required=True)
    init.add_argument("--constraint", action="append", default=[])
    init.add_argument(
        "--acceptance",
        action="append",
        required=True,
        help="Hard acceptance criterion; repeat for multiple criteria",
    )
    init.set_defaults(func=_cmd_init)

    status = commands.add_parser("status", help="Show durable ARUN state for this repository")
    status.add_argument("--root", default=".")
    status.add_argument("--project-id")
    status.set_defaults(func=_cmd_status)

    next_turn = commands.add_parser(
        "next",
        help="Create/resume the next bounded ARUN external-control turn",
    )
    next_turn.add_argument("--root", default=".")
    next_turn.add_argument("--project-id")
    next_turn.set_defaults(func=_cmd_next)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except ProjectModeError as exc:
        print(f"djobs project: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
