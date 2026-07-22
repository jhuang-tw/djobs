"""Idempotent one-time MCP and lifecycle-hook setup for Codex and Claude Code."""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from djobs.host_hooks import host_hook_doctor, install_host_hooks, remove_host_hooks
from djobs.workspace import shared_db_path

Runner = Callable[..., subprocess.CompletedProcess[str]]
Which = Callable[[str], str | None]


@dataclass(frozen=True)
class Host:
    name: str
    executable: str


def _server_command() -> list[str]:
    console = shutil.which("djobs-mcp")
    if console:
        return [console]
    return [sys.executable, "-m", "djobs.coding_mcp"]


def _host(name: str, *, which: Which = shutil.which) -> Host | None:
    executable = which(name)
    return Host(name=name, executable=executable) if executable else None


def _quoted(command: Sequence[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(list(command))
    return shlex.join(command)


def setup_command(host: str, db: Path, server: Sequence[str] | None = None) -> list[str]:
    """Build a copyable user-scope MCP registration command for one host."""

    server_command = list(server or _server_command())
    env_args = [
        "--env",
        f"DJOBS_DB={db.expanduser().resolve()}",
        "--env",
        f"DJOBS_AGENT_TYPE={host}",
    ]
    if host == "codex":
        return ["codex", "mcp", "add", "djobs", *env_args, "--", *server_command]
    if host == "claude":
        return [
            "claude",
            "mcp",
            "add",
            "djobs",
            "--scope",
            "user",
            *env_args,
            "--",
            *server_command,
        ]
    raise ValueError(f"unsupported host: {host}")


def _run(
    command: Sequence[str],
    *,
    runner: Runner = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    return runner(
        list(command),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def _exists(host: Host, *, runner: Runner = subprocess.run) -> bool:
    result = _run([host.executable, "mcp", "get", "djobs"], runner=runner)
    return result.returncode == 0


def configure_host(
    host_name: str,
    *,
    repair: bool = False,
    db: Path | None = None,
    runner: Runner = subprocess.run,
    which: Which = shutil.which,
    server: Sequence[str] | None = None,
    home: Path | None = None,
) -> dict[str, object]:
    """Configure MCP plus deterministic hooks while preserving unrelated config."""

    database = db or shared_db_path()
    host = _host(host_name, which=which)
    command = setup_command(host_name, database, server)
    if host is None:
        return {
            "host": host_name,
            "status": "manual",
            "command": _quoted(command),
            "message": f"{host_name} CLI was not found; lifecycle hooks were not installed",
        }

    mcp_status = "unchanged"
    mcp_message = "djobs MCP is already registered"
    already_registered = _exists(host, runner=runner)
    if already_registered and repair:
        remove = _run([host.executable, "mcp", "remove", "djobs"], runner=runner)
        if remove.returncode != 0:
            return {
                "host": host_name,
                "status": "manual",
                "command": _quoted(command),
                "message": remove.stderr.strip() or "could not remove the old djobs entry",
            }
        already_registered = False

    if not already_registered:
        executable_command = [host.executable, *command[1:]]
        result = _run(executable_command, runner=runner)
        if result.returncode != 0:
            return {
                "host": host_name,
                "status": "manual",
                "command": _quoted(command),
                "message": result.stderr.strip() or result.stdout.strip() or "registration failed",
            }
        mcp_status = "configured"
        mcp_message = "registered the shared local djobs MCP"

    try:
        hook_result = install_host_hooks(
            host_name,
            database,
            home=home,
            mode="smart",
            force=repair,
        )
    except ValueError as exc:
        return {
            "host": host_name,
            "status": "manual",
            "command": _quoted(command),
            "message": f"{mcp_message}; hook setup needs review: {exc}",
        }

    overall = "configured" if "configured" in {mcp_status, hook_result["status"]} else "unchanged"
    trust_note = " Open /hooks once to review and trust it." if host_name == "codex" else ""
    return {
        "host": host_name,
        "status": overall,
        "hooks": hook_result,
        "message": (
            f"{mcp_message}; automatic session/prompt/tool/stop hooks "
            f"{hook_result['status']} at {hook_result['path']}.{trust_note}"
        ),
    }


def remove_host(
    host_name: str,
    *,
    runner: Runner = subprocess.run,
    which: Which = shutil.which,
    home: Path | None = None,
) -> dict[str, object]:
    hook_result = remove_host_hooks(host_name, home=home)
    host = _host(host_name, which=which)
    if host is None:
        return {
            "host": host_name,
            "status": hook_result["status"],
            "message": f"host CLI was not found; hooks are {hook_result['status']}",
        }
    if not _exists(host, runner=runner):
        return {
            "host": host_name,
            "status": hook_result["status"],
            "message": f"djobs MCP was not registered; hooks are {hook_result['status']}",
        }
    result = _run([host.executable, "mcp", "remove", "djobs"], runner=runner)
    mcp_status = "removed" if result.returncode == 0 else "error"
    return {
        "host": host_name,
        "status": mcp_status,
        "message": (
            result.stderr.strip()
            or result.stdout.strip()
            or f"removed djobs MCP; hooks are {hook_result['status']}"
        ),
    }


def doctor_results(
    *,
    runner: Runner = subprocess.run,
    which: Which = shutil.which,
    home: Path | None = None,
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for name in ("codex", "claude"):
        host = _host(name, which=which)
        hook = host_hook_doctor(name, home=home)
        results.append(
            {
                "host": name,
                "available": host is not None,
                "registered": _exists(host, runner=runner) if host is not None else False,
                "hooks": hook["installed"],
                "hook_path": hook["path"],
            }
        )
    database = shared_db_path().expanduser()
    results.append(
        {
            "host": "shared-db",
            "available": database.parent.exists() or database.parent.parent.exists(),
            "registered": database.exists(),
            "path": str(database),
        }
    )
    return results


def print_setup_doctor() -> None:
    print("\nCross-agent setup:")
    for item in doctor_results():
        if item["host"] == "shared-db":
            print(f"  shared-db: {item['path']}")
            continue
        state = "registered" if item["registered"] else "not registered"
        available = "available" if item["available"] else "CLI not found"
        hooks = "hooks installed" if item["hooks"] else "hooks missing"
        print(f"  {item['host']}: {available}, {state}, {hooks}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="djobs setup")
    parser.add_argument("action", choices=["setup", "repair", "remove"])
    parser.add_argument("target", nargs="?", choices=["codex", "claude", "all"], default="all")
    args = parser.parse_args(argv)
    targets = ["codex", "claude"] if args.target == "all" else [args.target]

    for target in targets:
        if args.action == "remove":
            result = remove_host(target)
        else:
            result = configure_host(target, repair=args.action == "repair")
        print(f"{target}: {result['status']} — {result['message']}")
        if result.get("command"):
            print(result["command"])
    return 0
