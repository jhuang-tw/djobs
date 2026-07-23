"""One-time MCP plus passive-observation setup for coding-agent hosts."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from djobs.host_hooks import host_hook_doctor, install_host_hooks, remove_host_hooks
from djobs.workspace import shared_db_path

Runner = Callable[..., subprocess.CompletedProcess[str]]
Which = Callable[[str], str | None]
_CLIENTS = ("copilot", "codex", "claude", "gemini", "kimi")
_GEMINI_DJOBS_LINE_RE = re.compile(r"(?im)^\s*(?:[✓✗●○*+-]\s*)?djobs(?:\s|:)")
_COPILOT_TOOLS = "sync_workspace,checkpoint,handoff,resume_delta"


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
    """Build a copyable user-scope MCP registration command."""

    server_command = list(server or _server_command())
    database = db.expanduser().resolve()
    if host == "copilot":
        return [
            "copilot",
            "mcp",
            "add",
            "djobs",
            "--env",
            f"DJOBS_DB={database}",
            "--env",
            "DJOBS_AGENT_TYPE=copilot",
            "--tools",
            _COPILOT_TOOLS,
            "--",
            *server_command,
        ]
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
            "--scope",
            "user",
            "--env",
            f"DJOBS_DB={database}",
            "--env",
            "DJOBS_AGENT_TYPE=claude",
            "djobs",
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
        return result.returncode == 0 and bool(_GEMINI_DJOBS_LINE_RE.search(result.stdout))
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
        path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
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
    except (OSError, ValueError):
        return False
    servers = document.get("mcpServers")
    return isinstance(servers, dict) and isinstance(servers.get("djobs"), dict)


def _error_text(exc: BaseException) -> str:
    if isinstance(exc, subprocess.TimeoutExpired):
        return f"command timed out after {exc.timeout} seconds"
    return str(exc)


def _mcp_setup(
    host: Host,
    host_name: str,
    database: Path,
    command: list[str],
    *,
    repair: bool,
    runner: Runner,
    server: Sequence[str] | None,
    home: Path | None,
) -> tuple[str, str, str | None]:
    """Configure only MCP and return status, message, and optional error."""

    try:
        if host_name == "kimi":
            mcp = _install_kimi_mcp(database, server, home=home)
            status = str(mcp["status"])
            return status, f"Kimi MCP {status} at {mcp['path']}", None

        registered = _exists(host, runner=runner)
        if registered and repair:
            remove = _run(_remove_command(host), runner=runner)
            if remove.returncode != 0:
                message = remove.stderr.strip() or remove.stdout.strip()
                return "error", "existing djobs MCP was left unchanged", message or "remove failed"
            registered = False
        if registered:
            return "unchanged", "djobs MCP is already registered", None

        executable_command = [host.executable, *command[1:]]
        result = _run(executable_command, runner=runner)
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "registration failed"
            return "error", "djobs MCP registration failed", message
        return "configured", "registered the shared local djobs MCP", None
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return "error", "djobs MCP configuration needs review", _error_text(exc)


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
    """Configure one host's MCP and passive hooks independently."""

    database = db or shared_db_path()
    host = _host(host_name, which=which)
    command = setup_command(host_name, database, server)
    if host is None:
        return {
            "host": host_name,
            "status": "unavailable",
            "command": _quoted(command) if command else "",
            "message": f"{host_name} CLI was not found; no client configuration was changed",
        }

    mcp_status, mcp_message, mcp_error = _mcp_setup(
        host,
        host_name,
        database,
        command,
        repair=repair,
        runner=runner,
        server=server,
        home=home,
    )

    hook_result: dict[str, object] | None = None
    hook_error: str | None = None
    try:
        hook_result = install_host_hooks(
            host_name,
            database,
            home=home,
            mode="smart",
            force=repair,
        )
    except (OSError, ValueError) as exc:
        hook_error = str(exc)

    hook_status = str(hook_result["status"]) if hook_result is not None else "error"
    errors = [item for item in (mcp_error, hook_error) if item]
    configured = "configured" in {mcp_status, hook_status}
    if errors:
        overall = "partial" if mcp_status != "error" or hook_status != "error" else "error"
    else:
        overall = "configured" if configured else "unchanged"

    hook_message = (
        f"passive observation adapter {hook_status} at {hook_result['path']}"
        if hook_result is not None
        else f"passive observation adapter failed: {hook_error}"
    )
    notes: list[str] = []
    if host_name == "codex":
        notes.append("Open /hooks once to review and trust it")
    if host_name == "copilot":
        notes.append("This one adapter is shared by Copilot CLI and VS Code Agent")
        notes.append("Copilot cloud agent needs a remote or Git-backed djobs backend")
    suffix = f" {'; '.join(notes)}." if notes else ""
    error_note = f" Errors: {'; '.join(errors)}." if errors else ""
    return {
        "host": host_name,
        "status": overall,
        "command": _quoted(command) if mcp_error and command else "",
        "mcp": {"status": mcp_status, "error": mcp_error},
        "hooks": hook_result or {"host": host_name, "status": "error", "error": hook_error},
        "message": f"{mcp_message}; {hook_message}.{error_note}{suffix}",
    }


def _remove_hooks_safely(host_name: str, home: Path | None) -> dict[str, object]:
    try:
        return remove_host_hooks(host_name, home=home)
    except (OSError, ValueError) as exc:
        return {"host": host_name, "status": "error", "error": str(exc)}


def remove_host(
    host_name: str,
    *,
    runner: Runner = subprocess.run,
    which: Which = shutil.which,
    home: Path | None = None,
) -> dict[str, object]:
    hook_result = _remove_hooks_safely(host_name, home)
    hook_status = str(hook_result["status"])
    host = _host(host_name, which=which)

    if host_name == "kimi":
        try:
            mcp = _remove_kimi_mcp(home=home)
            mcp_status = str(mcp["status"])
            mcp_error = None
        except (OSError, ValueError) as exc:
            mcp_status = "error"
            mcp_error = str(exc)
        removed = "removed" in {mcp_status, hook_status}
        failed = "error" in {mcp_status, hook_status}
        status = (
            "partial"
            if removed and failed
            else "error"
            if failed
            else "removed"
            if removed
            else "absent"
        )
        error_note = f"; error: {mcp_error or hook_result.get('error')}" if failed else ""
        return {
            "host": host_name,
            "status": status,
            "message": f"Kimi MCP is {mcp_status}; hooks are {hook_status}{error_note}",
        }

    if host is None:
        return {
            "host": host_name,
            "status": hook_status if hook_status != "absent" else "unavailable",
            "message": f"host CLI was not found; hooks are {hook_status}",
        }

    try:
        registered = _exists(host, runner=runner)
    except (OSError, subprocess.SubprocessError) as exc:
        status = "partial" if hook_status == "removed" else "error"
        return {
            "host": host_name,
            "status": status,
            "message": f"hooks are {hook_status}; MCP detection failed: {_error_text(exc)}",
        }
    if not registered:
        return {
            "host": host_name,
            "status": hook_status,
            "message": f"djobs MCP was not registered; hooks are {hook_status}",
        }

    try:
        result = _run(_remove_command(host), runner=runner)
    except (OSError, subprocess.SubprocessError) as exc:
        status = "partial" if hook_status == "removed" else "error"
        return {
            "host": host_name,
            "status": status,
            "message": f"hooks are {hook_status}; MCP removal failed: {_error_text(exc)}",
        }
    if result.returncode == 0:
        return {
            "host": host_name,
            "status": "removed",
            "message": result.stderr.strip()
            or result.stdout.strip()
            or f"removed djobs MCP; hooks are {hook_status}",
        }
    status = "partial" if hook_status == "removed" else "error"
    message = result.stderr.strip() or result.stdout.strip() or "unknown error"
    return {
        "host": host_name,
        "status": status,
        "message": f"hooks are {hook_status}; MCP removal failed: {message}",
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
        error: str | None = None
        try:
            if name == "kimi":
                registered = _kimi_mcp_installed(home=home)
            else:
                registered = _exists(host, runner=runner) if host is not None else False
        except (OSError, subprocess.SubprocessError) as exc:
            registered = False
            error = _error_text(exc)
        results.append(
            {
                "host": name,
                "available": host is not None,
                "registered": registered,
                "hooks": hook["installed"],
                "hook_path": hook["path"],
                "error": error,
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
    print("\nAgent setup:")
    for item in doctor_results():
        if item["host"] == "shared-db":
            print(f"  shared-db: {item['path']}")
            continue
        state = "registered" if item["registered"] else "not registered"
        available = "available" if item["available"] else "CLI not found"
        hooks = "adapter installed" if item["hooks"] else "adapter missing"
        error = f", check failed: {item['error']}" if item.get("error") else ""
        print(f"  {item['host']}: {available}, {state}, {hooks}{error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="djobs setup")
    parser.add_argument("action", choices=["setup", "repair", "remove"])
    parser.add_argument("target", nargs="?", choices=[*_CLIENTS, "all"], default="copilot")
    args = parser.parse_args(argv)
    targets = list(_CLIENTS) if args.target == "all" else [args.target]
    failed = False

    for target in targets:
        if args.action == "remove":
            result = remove_host(target)
        else:
            result = configure_host(target, repair=args.action == "repair")
        status = str(result["status"])
        display_status = "skipped" if args.target == "all" and status == "unavailable" else status
        print(f"{target}: {display_status} — {result['message']}")
        if result.get("command"):
            print(result["command"])
        if status in {"partial", "error"} or (status == "unavailable" and args.target != "all"):
            failed = True
    return 1 if failed else 0
