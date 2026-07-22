"""Install user-scoped Codex and Claude Code lifecycle hooks safely."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

_MANAGED_TOKEN = "djobs.hook_entrypoint"
_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop")


def _command(argv: Sequence[str], *, windows: bool = False) -> str:
    return subprocess.list2cmdline(list(argv)) if windows else shlex.join(list(argv))


def _hook_argv(event: str, host: str, database: Path, mode: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "djobs.hook_entrypoint",
        event,
        "--host",
        host,
        "--db",
        str(database.expanduser().resolve()),
        "--mode",
        mode,
    ]


def _handler(event: str, host: str, database: Path, mode: str) -> dict[str, Any]:
    argv = _hook_argv(event, host, database, mode)
    item: dict[str, Any] = {
        "type": "command",
        "timeout": 15,
        "statusMessage": "Synchronizing djobs handoff state",
    }
    if host == "claude":
        # Claude Code supports exec form. It avoids shell quoting differences on
        # native Windows, Git Bash, macOS, and Linux.
        item["command"] = argv[0]
        item["args"] = argv[1:]
    else:
        item["command"] = _command(argv)
        item["commandWindows"] = _command(argv, windows=True)
    return item


def managed_hooks(
    host: str,
    database: Path,
    mode: str = "smart",
) -> dict[str, list[dict[str, Any]]]:
    if host not in {"codex", "claude"}:
        raise ValueError(f"unsupported host: {host}")
    return {
        "SessionStart": [
            {
                "matcher": "startup|resume|clear|compact",
                "hooks": [_handler("session-start", host, database, mode)],
            }
        ],
        "UserPromptSubmit": [{"hooks": [_handler("user-prompt", host, database, mode)]}],
        "PreToolUse": [
            {
                "matcher": "^Bash$",
                "hooks": [_handler("pre", host, database, mode)],
            }
        ],
        "PostToolUse": [
            {
                "matcher": "Bash|apply_patch|Edit|Write",
                "hooks": [_handler("post", host, database, mode)],
            }
        ],
        "Stop": [{"hooks": [_handler("stop", host, database, mode)]}],
    }


def hook_path(host: str, home: Path | None = None) -> Path:
    root = home or Path.home()
    if host == "codex":
        return root / ".codex" / "hooks.json"
    if host == "claude":
        return root / ".claude" / "settings.json"
    raise ValueError(f"unsupported host: {host}")


def _contains_managed(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return _MANAGED_TOKEN in json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _strip_managed(groups: Any) -> list[dict[str, Any]]:
    if not isinstance(groups, list):
        return []
    kept: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        copy = dict(group)
        handlers = copy.get("hooks")
        if isinstance(handlers, list):
            remaining = [item for item in handlers if not _contains_managed(item)]
            if remaining:
                copy["hooks"] = remaining
                kept.append(copy)
        elif not _contains_managed(copy):
            kept.append(copy)
    return kept


def _load(path: Path, *, force: bool) -> dict[str, Any]:
    del force  # Repair may replace djobs handlers, but never unrelated malformed settings.
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"refusing to modify malformed JSON at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"refusing to modify non-object JSON at {path}")
    return value


def _hooks_object(document: dict[str, Any], host: str) -> dict[str, Any]:
    hooks = document.get("hooks")
    if hooks is None:
        hooks = {}
        document["hooks"] = hooks
    if not isinstance(hooks, dict):
        raise ValueError(f"the hooks field for {host} is not an object")
    return hooks


def install_host_hooks(
    host: str,
    database: Path,
    *,
    home: Path | None = None,
    mode: str = "smart",
    force: bool = False,
) -> dict[str, Any]:
    """Merge djobs hooks while preserving every unrelated hook and setting."""

    path = hook_path(host, home)
    document = _load(path, force=force)
    hooks = _hooks_object(document, host)
    desired = managed_hooks(host, database, mode)
    before = json.dumps(document, ensure_ascii=False, sort_keys=True)
    for event in _EVENTS:
        existing = _strip_managed(hooks.get(event))
        hooks[event] = [*existing, *desired[event]]
    if host == "codex" and "description" not in document:
        document["description"] = "User hooks including automatic djobs cross-agent handoff."
    content = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    after = json.dumps(document, ensure_ascii=False, sort_keys=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    if before != after or not path.exists():
        path.write_text(content, encoding="utf-8")
        status = "configured"
    else:
        status = "unchanged"
    return {"host": host, "status": status, "path": str(path)}


def remove_host_hooks(host: str, *, home: Path | None = None) -> dict[str, Any]:
    path = hook_path(host, home)
    if not path.exists():
        return {"host": host, "status": "absent", "path": str(path)}
    document = _load(path, force=False)
    hooks = document.get("hooks")
    changed = False
    if isinstance(hooks, dict):
        for event in list(hooks):
            original = hooks[event]
            stripped = _strip_managed(original)
            if stripped != original:
                changed = True
                if stripped:
                    hooks[event] = stripped
                else:
                    hooks.pop(event, None)
        if not hooks:
            document.pop("hooks", None)
    if not changed:
        return {"host": host, "status": "absent", "path": str(path)}
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"host": host, "status": "removed", "path": str(path)}


def host_hook_doctor(host: str, *, home: Path | None = None) -> dict[str, Any]:
    path = hook_path(host, home)
    if not path.exists():
        return {"host": host, "installed": False, "path": str(path)}
    try:
        document = _load(path, force=False)
        hooks = document.get("hooks", {})
        text = json.dumps(hooks, separators=(",", ":"))
        installed = all(event in hooks for event in _EVENTS) and _MANAGED_TOKEN in text
    except ValueError:
        installed = False
    return {"host": host, "installed": installed, "path": str(path)}
