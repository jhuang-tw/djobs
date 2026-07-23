"""Install host-specific lifecycle adapters around the client-neutral core."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

_MANAGED_TOKEN = "djobs.hook_entrypoint"
_KIMI_BEGIN = "# >>> djobs managed observation hooks >>>"
_KIMI_END = "# <<< djobs managed observation hooks <<<"
_JSON_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",
    "PreCompact",
    "PreCompress",
    "AfterTool",
    "SessionEnd",
)
_COPILOT_EVENTS = (
    "SessionStart",
    "PostToolUse",
    "PostToolUseFailure",
    "PreCompact",
    "SessionEnd",
)
_SUPPORTED = ("copilot", "codex", "claude", "gemini", "kimi")


def _command(argv: Sequence[str], *, windows: bool | None = None) -> str:
    """Quote one hook command for the target shell."""

    use_windows = os.name == "nt" if windows is None else windows
    return subprocess.list2cmdline(list(argv)) if use_windows else shlex.join(list(argv))


def _hook_argv(
    event: str,
    client: str,
    database: Path,
    mode: str,
    *,
    output: str = "json",
) -> list[str]:
    argv = [
        sys.executable,
        "-m",
        "djobs.hook_entrypoint",
        event,
        "--client",
        client,
        "--db",
        str(database.expanduser().resolve()),
        "--mode",
        mode,
    ]
    if output != "json":
        argv.extend(["--output", output])
    return argv


def _json_handler(event: str, client: str, database: Path, mode: str) -> dict[str, Any]:
    argv = _hook_argv(event, client, database, mode)
    if client == "claude":
        return {
            "type": "command",
            "command": argv[0],
            "args": argv[1:],
            "timeout": 15,
            "statusMessage": "Recording djobs repository observations",
        }
    command = _command(argv)
    if client == "gemini":
        return {
            "type": "command",
            "command": command,
            "name": f"djobs-{event}",
            "timeout": 15000,
            "description": "Record read-only repository observations for cross-agent context.",
        }
    return {
        "type": "command",
        "command": _command(argv, windows=False),
        "commandWindows": _command(argv, windows=True),
        "timeout": 15,
        "statusMessage": "Recording djobs repository observations",
    }


def _copilot_handler(
    event: str,
    database: Path,
    mode: str,
    *,
    matcher: str | None = None,
) -> dict[str, Any]:
    """Return one Copilot CLI/VS Code compatible command hook."""

    argv = _hook_argv(event, "copilot", database, mode)
    handler: dict[str, Any] = {
        "type": "command",
        "bash": _command(argv, windows=False),
        "powershell": _command(argv, windows=True),
        "timeoutSec": 15,
    }
    if matcher:
        handler["matcher"] = matcher
    return handler


def _copilot_document(database: Path, mode: str) -> dict[str, Any]:
    """Use PascalCase events so Copilot sends VS Code-compatible snake_case payloads."""

    return {
        "version": 1,
        "hooks": {
            "SessionStart": [_copilot_handler("session-start", database, mode)],
            "PostToolUse": [
                _copilot_handler(
                    "post",
                    database,
                    mode,
                    matcher="bash|edit|create_file|replace_string_in_file|apply_patch|write_file",
                )
            ],
            "PostToolUseFailure": [
                _copilot_handler(
                    "post-failure",
                    database,
                    mode,
                    matcher="bash|edit|create_file|replace_string_in_file|apply_patch|write_file",
                )
            ],
            "PreCompact": [_copilot_handler("pre-compact", database, mode)],
            "SessionEnd": [_copilot_handler("session-end", database, mode)],
        },
    }


def _specs(client: str) -> tuple[tuple[str, str, str | None], ...]:
    """Map native hook events to the normalized djobs event protocol."""

    if client == "codex":
        return (
            ("SessionStart", "session-start", "startup|resume|clear|compact"),
            ("PostToolUse", "post", "Bash|apply_patch|Edit|Write"),
            ("PreCompact", "pre-compact", "manual|auto"),
            ("SessionEnd", "session-end", None),
        )
    if client == "claude":
        return (
            ("SessionStart", "session-start", "startup|resume|clear|compact"),
            ("PostToolUse", "post", "Bash|apply_patch|Edit|Write"),
            ("PostToolUseFailure", "post-failure", "Bash|apply_patch|Edit|Write"),
            ("PreCompact", "pre-compact", "manual|auto"),
            ("SessionEnd", "session-end", None),
        )
    if client == "gemini":
        return (
            ("SessionStart", "session-start", None),
            ("AfterTool", "post", "run_shell_command|write_file|replace|write_.*"),
            ("PreCompress", "pre-compact", None),
            ("SessionEnd", "session-end", None),
        )
    if client == "kimi":
        return (
            ("SessionStart", "session-prepare", "startup|resume"),
            ("UserPromptSubmit", "prompt-context", None),
            ("PostToolUse", "post", "Bash|Write|Edit|apply_patch"),
            ("PostToolUseFailure", "post-failure", "Bash|Write|Edit|apply_patch"),
            ("PreCompact", "pre-compact", "manual|auto"),
            ("SessionEnd", "session-end", "exit"),
        )
    raise ValueError(f"unsupported client adapter: {client}")


def managed_hooks(
    client: str,
    database: Path,
    mode: str = "smart",
) -> dict[str, list[dict[str, Any]]]:
    """Return host-native passive observation hooks for nested JSON clients."""

    if client in {"copilot", "kimi"}:
        raise ValueError(f"{client} uses a dedicated hook format; call install_host_hooks")
    desired: dict[str, list[dict[str, Any]]] = {}
    for native_event, normalized_event, matcher in _specs(client):
        group: dict[str, Any] = {
            "hooks": [_json_handler(normalized_event, client, database, mode)]
        }
        if matcher is not None:
            group["matcher"] = matcher
        desired.setdefault(native_event, []).append(group)
    return desired


def hook_path(client: str, home: Path | None = None) -> Path:
    root = home or Path.home()
    if client == "copilot":
        return root / ".copilot" / "hooks" / "djobs.json"
    if client == "codex":
        return root / ".codex" / "hooks.json"
    if client == "claude":
        return root / ".claude" / "settings.json"
    if client == "gemini":
        return root / ".gemini" / "settings.json"
    if client == "kimi":
        return root / ".kimi-code" / "config.toml"
    raise ValueError(f"unsupported client adapter: {client}")


def _contains_managed(value: Any) -> bool:
    return isinstance(value, dict) and _MANAGED_TOKEN in json.dumps(
        value, ensure_ascii=False, separators=(",", ":")
    )


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


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"refusing to modify malformed JSON at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"refusing to modify non-object JSON at {path}")
    return value


def _install_copilot_hooks(
    database: Path,
    *,
    home: Path | None,
    mode: str,
) -> dict[str, Any]:
    path = hook_path("copilot", home)
    desired = _copilot_document(database, mode)
    if path.exists():
        current = _load_json(path)
        if not _contains_managed(current):
            raise ValueError(f"refusing to replace unmanaged Copilot hook file at {path}")
        before = json.dumps(current, ensure_ascii=False, sort_keys=True)
    else:
        before = ""
    after = json.dumps(desired, ensure_ascii=False, sort_keys=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    if before != after:
        path.write_text(json.dumps(desired, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        status = "configured"
    else:
        status = "unchanged"
    return {"host": "copilot", "status": status, "path": str(path)}


def _install_json_hooks(
    client: str,
    database: Path,
    *,
    home: Path | None,
    mode: str,
) -> dict[str, Any]:
    path = hook_path(client, home)
    document = _load_json(path)
    hooks = document.get("hooks")
    if hooks is None:
        hooks = {}
        document["hooks"] = hooks
    if not isinstance(hooks, dict):
        raise ValueError(f"the hooks field for {client} is not an object")
    desired = managed_hooks(client, database, mode)
    before = json.dumps(document, ensure_ascii=False, sort_keys=True)
    for event in _JSON_EVENTS:
        combined = [*_strip_managed(hooks.get(event)), *desired.get(event, [])]
        if combined:
            hooks[event] = combined
        else:
            hooks.pop(event, None)
    if client == "codex" and "description" not in document:
        document["description"] = "User hooks including passive djobs observations."
    content = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    after = json.dumps(document, ensure_ascii=False, sort_keys=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    if before != after or not path.exists():
        path.write_text(content, encoding="utf-8")
        status = "configured"
    else:
        status = "unchanged"
    return {"host": client, "status": status, "path": str(path)}


def _kimi_block(database: Path, mode: str) -> str:
    lines = [_KIMI_BEGIN]
    for native_event, normalized_event, matcher in _specs("kimi"):
        output = "plain" if normalized_event == "prompt-context" else "silent"
        command = _command(_hook_argv(normalized_event, "kimi", database, mode, output=output))
        lines.extend(["[[hooks]]", f"event = {json.dumps(native_event)}"])
        if matcher is not None:
            lines.append(f"matcher = {json.dumps(matcher)}")
        lines.extend(
            [
                f"command = {json.dumps(command)}",
                "timeout = 15",
                "",
            ]
        )
    lines.append(_KIMI_END)
    return "\n".join(lines) + "\n"


def _strip_kimi_block(content: str) -> tuple[str, bool]:
    begin = content.find(_KIMI_BEGIN)
    end = content.find(_KIMI_END)
    if begin < 0 and end < 0:
        return content, False
    if begin < 0 or end < begin:
        raise ValueError("refusing to modify a malformed djobs block in Kimi config")
    end += len(_KIMI_END)
    if end < len(content) and content[end] == "\n":
        end += 1
    prefix = content[:begin].rstrip()
    separator = "\n" if prefix else ""
    return prefix + separator + content[end:].lstrip(), True


def _install_kimi_hooks(
    database: Path,
    *,
    home: Path | None,
    mode: str,
) -> dict[str, Any]:
    path = hook_path("kimi", home)
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    base, _ = _strip_kimi_block(original)
    content = base.rstrip()
    if content:
        content += "\n\n"
    content += _kimi_block(database, mode)
    path.parent.mkdir(parents=True, exist_ok=True)
    if content != original:
        path.write_text(content, encoding="utf-8")
        status = "configured"
    else:
        status = "unchanged"
    return {"host": "kimi", "status": status, "path": str(path)}


def install_host_hooks(
    host: str,
    database: Path,
    *,
    home: Path | None = None,
    mode: str = "smart",
    force: bool = False,
) -> dict[str, Any]:
    """Merge only djobs-managed adapters, preserving unrelated configuration."""

    del force
    if host not in _SUPPORTED:
        raise ValueError(f"unsupported client adapter: {host}")
    if host == "copilot":
        return _install_copilot_hooks(database, home=home, mode=mode)
    if host == "kimi":
        return _install_kimi_hooks(database, home=home, mode=mode)
    return _install_json_hooks(host, database, home=home, mode=mode)


def remove_host_hooks(host: str, *, home: Path | None = None) -> dict[str, Any]:
    path = hook_path(host, home)
    if not path.exists():
        return {"host": host, "status": "absent", "path": str(path)}
    if host == "copilot":
        document = _load_json(path)
        if not _contains_managed(document):
            return {"host": host, "status": "absent", "path": str(path)}
        path.unlink()
        return {"host": host, "status": "removed", "path": str(path)}
    if host == "kimi":
        original = path.read_text(encoding="utf-8")
        content, changed = _strip_kimi_block(original)
        if not changed:
            return {"host": host, "status": "absent", "path": str(path)}
        path.write_text(content, encoding="utf-8")
        return {"host": host, "status": "removed", "path": str(path)}

    document = _load_json(path)
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
        if host == "copilot":
            document = _load_json(path)
            hooks = document.get("hooks", {})
            installed = (
                document.get("version") == 1
                and isinstance(hooks, dict)
                and set(_COPILOT_EVENTS).issubset(hooks)
                and _contains_managed(document)
            )
        elif host == "kimi":
            text = path.read_text(encoding="utf-8")
            installed = _KIMI_BEGIN in text and _KIMI_END in text and _MANAGED_TOKEN in text
        else:
            document = _load_json(path)
            hooks = document.get("hooks", {})
            text = json.dumps(hooks, separators=(",", ":"))
            required = {event for event, _normalized, _matcher in _specs(host)}
            installed = required.issubset(hooks) and _MANAGED_TOKEN in text
    except (OSError, ValueError):
        installed = False
    return {"host": host, "installed": installed, "path": str(path)}
