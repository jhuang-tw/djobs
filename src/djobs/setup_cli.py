"""One-time MCP plus passive-observation setup for major coding-agent clients."""

from __future__ import annotations

import argparse
import json
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
_CLIENTS = ("codex", "claude", "gemini", "kimi")


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
    """Build a copyable user-scope MCP registration command where one exists."""

    server_command = list(server or _server_command())
    database = db.expanduser().resolve()
    if host == "codex":
        return [
            "codex",
            "mcp",
            "add",
            "djobs",
            "--env",
            f"DJOBS_DB={database}",
            "--env",
            "DJOBS_AGENT_TYPE=codex",
            "--",
            *server_command,
        ]
    if host == "claude":
        return [
            "claude",
            "mcp",
            "add",
            "djobs",
            "--scope",
            "user",
            "--env",
            f"DJOBS_DB={database}",
            "--env",
            "DJOBS_AGENT_TYPE=claude",
            "--",
            *server_command,
        ]
    if host == "gemini":
        return [
            "gemini",
            "mcp",
            "add",
            "--scope",
            "user",
            "--env",
            f"DJOBS_DB={database}",
            "--env",
            "DJOBS_AGENT_TYPE=gemini",
            "djobs",
            *server_command,
        ]
    if host == "kimi":
        return []
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
    if host.name == "gemini":
        result = _run([host.executable, "mcp", "list"], runner=runner)
        return result.returncode == 0 and "djobs" in result.stdout.lower()
    result = _run([host.executable, "mcp", "get", "djobs"], runner=runner)
    return result.returncode == 0


def _remove_command(host: Host) -> list[str]:
    if host.name == "gemini":
        return [host.executable, "mcp", "remove", "djobs", "--scope", "user"]
    return [host.executable, "mcp", "remove", "djobs"]


def _kimi_mcp_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".kimi-code" / "mcp.json"


def _load_json_object(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"refusing to modify malformed JSON at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"refusing to modify non-object JSON at {path}")
    return value


def _install_kimi_mcp(
    database: Path,
    server: Sequence[str] | None,
    *,
    home: Path | None,
) -> dict[str, object]:
    path = _kimi_mcp_path(home)
    document = _load_json_object(path)
    servers = document.get("mcpServers")
    if servers is None:
        servers = {}
        document["mcpServers"] = servers
    if not isinstance(servers, dict):
        raise ValueError(f"the mcpServers field in {path} is not an object")
    command = list(server or _server_command())
    desired = {
        "command": command[0],
        "args": command[1:],
        "env": {
            "DJOBS_DB": str(database.expanduser().resolve()),
            "DJOBS_AGENT_TYPE": "kimi",
        },
    }
    before = json.dumps(document, ensure_ascii=False, sort_keys=True)
    servers["djobs"] = desired
    after = json.dumps(document, ensure_ascii=False, sort_keys=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    if before != after or not path.exists():
        content = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        path.write_text(content, encoding="utf-8")
        status = "configured"
    else:
        status = "unchanged"
    return {"status": status, "path": str(path)}


def _remove_kimi_mcp(*, home: Path | None) -> dict[str, object]:
    path = _kimi_mcp_path(home)
    if not path.exists():
        return {"status": "absent", "path": str(path)}
    document = _load_json_object(path)
    servers = document.get("mcpServers")
    if not isinstance(servers, dict) or "djobs" not in servers:
        return {"status": "absent", "path": str(path)}
    servers.pop("djobs", None)
    if not servers:
        document.pop("mcpServers", None)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"status": "removed", "path": str(path)}


def _kimi_mcp_installed(*, home: Path | None) -> bool:
    try:
        document = _load_json_object(_kimi_mcp_path(home))
    except ValueError:
        return False
    servers = document.get("mcpServers")
    return isinstance(servers, dict) and isinstance(servers.get("djobs"), dict)


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
    """Configure MCP plus a thin observation adapter, preserving unrelated config."""

    database = db or shared_db_path()
    host = _host(host_name, which=which)
    command = setup_command(host_name, database, server)
    if host is None:
        return {
            "host": host_name,
            "status": "manual",
            "command": _quoted(command) if command else "",
            "message": f"{host_name} CLI was not found; no client configuration was changed",
        }

    try:
        if host_name == "kimi":
            mcp = _install_kimi_mcp(database, server, home=home)
            mcp_status = str(mcp["status"])
            mcp_message = f"Kimi MCP {mcp_status} at {mcp['path']}"
        else:
            registered = _exists(host, runner=runner)
            if registered and repair:
                remove = _run(_remove_command(host), runner=runner)
                if remove.returncode != 0:
                    raise ValueError(remove.stderr.strip() or "could not remove old djobs MCP")
                registered = False
            if registered:
                mcp_status = "unchanged"
                mcp_message = "djobs MCP is already registered"
            else:
                executable_command = [host.executable, *command[1:]]
                result = _run(executable_command, runner=runner)
                if result.returncode != 0:
                    return {
                        "host": host_name,
                        "status": "manual",
                        "command": _quoted(command),
                        "message": result.stderr.strip()
                        or result.stdout.strip()
                        or "registration failed",
                    }
                mcp_status = "configured"
                mcp_message = "registered the shared local djobs MCP"

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
            "command": _quoted(command) if command else "",
            "message": f"configuration needs review: {exc}",
        }

    overall = "configured" if "configured" in {mcp_status, hook_result["status"]} else "unchanged"
    trust_note = " Open /hooks once to review and trust it." if host_name == "codex" else ""
    return {
        "host": host_name,
        "status": overall,
        "hooks": hook_result,
        "message": (
            f"{mcp_message}; passive session/tool observation adapter "
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
    if host_name == "kimi":
        try:
            mcp = _remove_kimi_mcp(home=home)
        except ValueError as exc:
            return {"host": host_name, "status": "error", "message": str(exc)}
        overall = "removed" if "removed" in {mcp["status"], hook_result["status"]} else "absent"
        return {
            "host": host_name,
            "status": overall,
            "message": f"Kimi MCP is {mcp['status']}; hooks are {hook_result['status']}",
        }
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
    result = _run(_remove_command(host), runner=runner)
    mcp_status = "removed" if result.returncode == 0 else "error"
    return {
        "host": host_name,
        "status": mcp_status,
        "message": result.stderr.strip()
        or result.stdout.strip()
        or f"removed djobs MCP; hooks are {hook_result['status']}",
    }


def doctor_results(
    *,
    runner: Runner = subprocess.run,
    which: Which = shutil.which,
    home: Path | None = None,
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for name in _CLIENTS:
        host = _host(name, which=which)
        hook = host_hook_doctor(name, home=home)
        if name == "kimi":
            registered = _kimi_mcp_installed(home=home)
        else:
            registered = _exists(host, runner=runner) if host is not None else False
        results.append(
            {
                "host": name,
                "available": host is not None,
                "registered": registered,
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
        hooks = "adapter installed" if item["hooks"] else "adapter missing"
        print(f"  {item['host']}: {available}, {state}, {hooks}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="djobs setup")
    parser.add_argument("action", choices=["setup", "repair", "remove"])
    parser.add_argument("target", nargs="?", choices=[*_CLIENTS, "all"], default="all")
    args = parser.parse_args(argv)
    targets = list(_CLIENTS) if args.target == "all" else [args.target]

    for target in targets:
        if args.action == "remove":
            result = remove_host(target)
        else:
            result = configure_host(target, repair=args.action == "repair")
        print(f"{target}: {result['status']} — {result['message']}")
        if result.get("command"):
            print(result["command"])
    return 0
