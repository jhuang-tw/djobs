"""Idempotent one-time MCP setup for Codex and Claude Code."""

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
    """Build a copyable user-scope registration command for one host."""

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
) -> dict[str, object]:
    """Configure only the ``djobs`` entry, preserving every other MCP server."""

    database = db or shared_db_path()
    host = _host(host_name, which=which)
    command = setup_command(host_name, database, server)
    if host is None:
        return {
            "host": host_name,
            "status": "manual",
            "command": _quoted(command),
            "message": f"{host_name} CLI was not found",
        }

    if _exists(host, runner=runner):
        if not repair:
            return {
                "host": host_name,
                "status": "unchanged",
                "message": "djobs is already registered; other servers were untouched",
            }
        remove = _run([host.executable, "mcp", "remove", "djobs"], runner=runner)
        if remove.returncode != 0:
            return {
                "host": host_name,
                "status": "manual",
                "command": _quoted(command),
                "message": remove.stderr.strip() or "could not remove the old djobs entry",
            }

    executable_command = [host.executable, *command[1:]]
    result = _run(executable_command, runner=runner)
    if result.returncode != 0:
        return {
            "host": host_name,
            "status": "manual",
            "command": _quoted(command),
            "message": result.stderr.strip() or result.stdout.strip() or "registration failed",
        }
    return {
        "host": host_name,
        "status": "configured",
        "message": "registered the shared local djobs MCP without changing other servers",
    }


def remove_host(
    host_name: str,
    *,
    runner: Runner = subprocess.run,
    which: Which = shutil.which,
) -> dict[str, object]:
    host = _host(host_name, which=which)
    if host is None:
        return {"host": host_name, "status": "absent", "message": "host CLI was not found"}
    if not _exists(host, runner=runner):
        return {"host": host_name, "status": "absent", "message": "djobs was not registered"}
    result = _run([host.executable, "mcp", "remove", "djobs"], runner=runner)
    return {
        "host": host_name,
        "status": "removed" if result.returncode == 0 else "error",
        "message": result.stderr.strip() or result.stdout.strip() or "removed djobs",
    }


def doctor_results(
    *,
    runner: Runner = subprocess.run,
    which: Which = shutil.which,
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for name in ("codex", "claude"):
        host = _host(name, which=which)
        if host is None:
            results.append({"host": name, "available": False, "registered": False})
            continue
        results.append(
            {"host": name, "available": True, "registered": _exists(host, runner=runner)}
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
        print(f"  {item['host']}: {available}, {state}")


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
