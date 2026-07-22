"""Automatic command rewriting and durable checkpoints for coding-agent hooks.

The hook protocol is intentionally host-tolerant. It accepts both GitHub
Copilot's camelCase payloads and the PascalCase/VS Code compatible snake_case
payloads. Shell tool calls are rewritten to ``djobs hook run`` before execution,
so checkpointing no longer depends on the model remembering to call an MCP tool.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from djobs.core.correlation import correlation_id_variants
from djobs.core.pause import is_paused
from djobs.queue.service import QueueService
from djobs.storage.sqlite import SQLiteJobRepository

_HOOK_RELATIVE_PATH = Path(".github/hooks/djobs.json")
_INCOMPLETE_STATUSES = ("pending", "running", "retry_scheduled")
_VALID_MODES = {"off", "smart", "all"}
_MAX_COMMAND_CHARS = 4000

_STATE_ONLY_RE = re.compile(
    r"^\s*(?:cd|pushd|popd|export|source|alias|unalias|set\s+[A-Za-z_][A-Za-z0-9_]*=)\b"
    r"[^;&|]*$",
    re.IGNORECASE,
)

_READ_ONLY_PREFIXES = (
    "cat ",
    "dir",
    "echo ",
    "env",
    "find ",
    "git diff",
    "git log",
    "git show",
    "git status",
    "grep ",
    "head ",
    "ls",
    "pwd",
    "rg ",
    "tail ",
    "tree",
    "type ",
    "where ",
    "which ",
    "whoami",
)

_MEANINGFUL_PATTERNS = (
    " pytest",
    "python -m pytest",
    "python -m unittest",
    "tox",
    "nox",
    "npm test",
    "npm run test",
    "npm run build",
    "npm run lint",
    "npm run check",
    "npm run typecheck",
    "pnpm test",
    "pnpm build",
    "pnpm lint",
    "pnpm check",
    "yarn test",
    "yarn build",
    "yarn lint",
    "bun test",
    "cargo test",
    "cargo build",
    "cargo check",
    "cargo clippy",
    "go test",
    "go build",
    "go vet",
    "dotnet test",
    "dotnet build",
    "mvn test",
    "mvn package",
    "gradle test",
    "gradle build",
    "./gradlew test",
    "./gradlew build",
    "make",
    "cmake --build",
    "ninja",
    "docker build",
    "docker compose build",
    "docker compose up",
    "terraform plan",
    "terraform apply",
    "ansible-playbook",
    "pre-commit run",
    "ruff check",
    "ruff format",
    "mypy",
    "eslint",
    "tsc",
    "vitest",
    "jest",
    "playwright test",
)


def _debug(message: str) -> None:
    if os.environ.get("DJOBS_HOOK_DEBUG") == "1":
        print(f"djobs hook: {message}", file=sys.stderr)


def _resolve_db_path(cwd: str) -> Path:
    configured = os.environ.get("DJOBS_DB")
    if not configured:
        return Path(cwd) / "djobs_mcp.db"
    path = Path(configured).expanduser()
    return path if path.is_absolute() else Path(cwd) / path


def _normalise_mode(value: str | None) -> str:
    mode = (value or os.environ.get("DJOBS_HOOK_MODE") or "smart").strip().lower()
    return mode if mode in _VALID_MODES else "smart"


def _read_payload() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("hook input must be a JSON object")
    return value


def _payload_cwd(payload: dict[str, Any]) -> str:
    raw = payload.get("cwd")
    if isinstance(raw, str) and raw.strip():
        return str(Path(raw).expanduser())
    return os.getcwd()


def _payload_session_id(payload: dict[str, Any]) -> str | None:
    raw = payload.get("sessionId", payload.get("session_id"))
    return raw if isinstance(raw, str) and raw else None


def _payload_timestamp(payload: dict[str, Any]) -> str | int | float | None:
    raw = payload.get("timestamp")
    return raw if isinstance(raw, (str, int, float)) and not isinstance(raw, bool) else None


def _tool_name(payload: dict[str, Any]) -> str:
    raw = payload.get("toolName", payload.get("tool_name", ""))
    return raw if isinstance(raw, str) else ""


def _tool_args(payload: dict[str, Any]) -> dict[str, Any] | None:
    raw = payload.get("toolArgs", payload.get("tool_input"))
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return dict(parsed) if isinstance(parsed, dict) else None
    return None


def _command_field(args: dict[str, Any]) -> str | None:
    for field in ("command", "cmd", "script"):
        if isinstance(args.get(field), str):
            return field
    return None


def _shell_kind(tool_name: str) -> str | None:
    lowered = tool_name.strip().lower()
    if lowered in {"bash", "shell"}:
        return "bash"
    if lowered in {"powershell", "pwsh"}:
        return "powershell"
    return None


def _is_state_only(command: str) -> bool:
    return bool(_STATE_ONLY_RE.match(command))


def _looks_meaningful(command: str) -> bool:
    compact = " ".join(command.strip().lower().split())
    if not compact:
        return False
    if compact == "djobs" or compact.startswith("djobs "):
        return False
    if _is_state_only(compact):
        return False
    if any(compact == prefix.rstrip() or compact.startswith(prefix) for prefix in _READ_ONLY_PREFIXES):
        return False

    padded = f" {compact}"
    if any(pattern in padded for pattern in _MEANINGFUL_PATTERNS):
        return True
    if len(compact) >= 160 and any(operator in compact for operator in ("&&", "||", ";")):
        return True
    return False


def _should_rewrite(command: str, mode: str) -> bool:
    if mode == "off" or _is_state_only(command):
        return False
    if command.strip().lower().startswith("djobs hook run"):
        return False
    return mode == "all" or _looks_meaningful(command)


def _encode_envelope(value: dict[str, Any]) -> str:
    raw = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_envelope(value: str) -> dict[str, Any]:
    padding = "=" * (-len(value) % 4)
    decoded = base64.urlsafe_b64decode((value + padding).encode("ascii"))
    result = json.loads(decoded.decode("utf-8"))
    if not isinstance(result, dict):
        raise ValueError("encoded hook payload must contain an object")
    return result


def rewrite_pre_tool_payload(
    payload: dict[str, Any],
    *,
    mode: str | None = None,
) -> dict[str, Any]:
    """Return a preToolUse decision, rewriting meaningful shell commands."""

    shell = _shell_kind(_tool_name(payload))
    args = _tool_args(payload)
    if shell is None or args is None:
        return {}

    field = _command_field(args)
    if field is None:
        return {}
    command = args[field]
    assert isinstance(command, str)

    resolved_mode = _normalise_mode(mode)
    cwd = _payload_cwd(payload)
    if is_paused(_resolve_db_path(cwd)) or not _should_rewrite(command, resolved_mode):
        return {}

    envelope = {
        "command": command,
        "shell": shell,
        "cwd": cwd,
        "session_id": _payload_session_id(payload),
        "timestamp": _payload_timestamp(payload),
        "mode": resolved_mode,
    }
    args[field] = f"djobs hook run --payload {_encode_envelope(envelope)}"
    return {"permissionDecision": "allow", "modifiedArgs": args}


def _command_label(command: str) -> str:
    label = " ".join(command.strip().split())
    return label if len(label) <= 180 else f"{label[:179]}…"


def _execute_command(command: str, shell: str, cwd: str) -> int:
    if shell == "powershell":
        executable = shutil.which("pwsh") or shutil.which("powershell")
        if executable is None:
            print("djobs hook: PowerShell executable not found", file=sys.stderr)
            return 127
        argv = [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command]
    else:
        executable = shutil.which("bash")
        if executable is not None:
            argv = [executable, "-lc", command]
        elif os.name == "nt":
            argv = ["cmd.exe", "/d", "/s", "/c", command]
        else:
            argv = ["/bin/sh", "-lc", command]
    return subprocess.run(argv, cwd=cwd, check=False).returncode


def run_wrapped_payload(payload: dict[str, Any]) -> int:
    """Execute the original command while recording a durable checkpoint."""

    command = payload.get("command")
    shell = payload.get("shell")
    cwd = payload.get("cwd")
    if not isinstance(command, str) or not isinstance(shell, str) or not isinstance(cwd, str):
        print("djobs hook: invalid wrapped command payload", file=sys.stderr)
        return 2

    task_id: str | None = None
    queue: QueueService | None = None
    started = time.monotonic()
    db_path = _resolve_db_path(cwd)

    if not is_paused(db_path):
        try:
            queue = QueueService(SQLiteJobRepository.from_path(db_path))
            job = queue.submit(
                "auto-command",
                {
                    "summary": _command_label(command),
                    "command": command[:_MAX_COMMAND_CHARS],
                    "shell": shell,
                    "cwd": cwd,
                    "session_id": payload.get("session_id"),
                    "source": "preToolUse",
                },
                correlation_id=cwd,
                max_attempts=1,
            )
            task_id = job.id
        except Exception as exc:  # checkpoint failure must never block the user's command
            _debug(f"checkpoint creation failed: {exc}")

    try:
        return_code = _execute_command(command, shell, cwd)
    except KeyboardInterrupt:
        return_code = 130
    except Exception as exc:
        _debug(f"command wrapper failed: {exc}")
        return_code = 127

    elapsed = time.monotonic() - started
    if queue is not None and task_id is not None:
        try:
            if return_code == 0:
                queue.complete(
                    task_id,
                    evidence=f"automatic command checkpoint: exit 0 in {elapsed:.2f}s",
                )
            else:
                queue.fail(task_id, f"automatic command checkpoint: exit {return_code}")
        except Exception as exc:
            _debug(f"checkpoint finalization failed: {exc}")
    return return_code


def session_start_context(payload: dict[str, Any]) -> dict[str, Any]:
    """Inject a compact recovery capsule when unfinished work exists."""

    cwd = _payload_cwd(payload)
    db_path = _resolve_db_path(cwd)
    if is_paused(db_path) or not db_path.exists():
        return {}

    try:
        repo = SQLiteJobRepository.from_path(db_path)
        jobs = repo.list_jobs_by_correlation_ids(
            correlation_id_variants(cwd),
            statuses=_INCOMPLETE_STATUSES,
        )
    except Exception as exc:
        _debug(f"session recovery failed: {exc}")
        return {}

    if not jobs:
        return {}

    shown = jobs[:5]
    lines = [
        f"djobs recovered {len(jobs)} unfinished checkpoint(s) for this workspace.",
        "Continue from these checkpoints instead of repeating completed work:",
    ]
    for job in shown:
        summary = job.payload.get("summary") if isinstance(job.payload, dict) else None
        label = summary if isinstance(summary, str) and summary else job.type
        lines.append(f"- [{job.status.value}] {label}")
    if len(jobs) > len(shown):
        lines.append(f"- ... and {len(jobs) - len(shown)} more; use resume_capsule for details.")
    return {"additionalContext": "\n".join(lines)}


def _hook_config(mode: str) -> dict[str, Any]:
    bash_guard = "if command -v djobs >/dev/null 2>&1; then djobs hook {event} || true; fi"
    powershell_guard = (
        "if (Get-Command djobs -ErrorAction SilentlyContinue) {{ "
        "djobs hook {event}; if ($LASTEXITCODE -ne 0) {{ exit 0 }} }}"
    )

    def command_hook(event: str, *, matcher: str | None = None) -> dict[str, Any]:
        item: dict[str, Any] = {
            "type": "command",
            "bash": bash_guard.format(event=event),
            "powershell": powershell_guard.format(event=event),
            "timeoutSec": 10,
            "env": {"DJOBS_HOOK_MODE": mode},
        }
        if matcher is not None:
            item["matcher"] = matcher
        return item

    return {
        "version": 1,
        "hooks": {
            "SessionStart": [command_hook("session-start")],
            "PreToolUse": [command_hook("pre", matcher="Bash")],
        },
    }


def install_hooks(
    root: Path | None = None,
    *,
    mode: str = "smart",
    force: bool = False,
) -> Path:
    """Install an idempotent repository hook config for compatible coding agents."""

    target = (root or Path.cwd()) / _HOOK_RELATIVE_PATH
    resolved_mode = _normalise_mode(mode)
    content = json.dumps(_hook_config(resolved_mode), indent=2) + "\n"

    if target.exists() and not force:
        existing = target.read_text(encoding="utf-8")
        if existing == content:
            print(f"djobs automatic hooks already up to date in {target}")
            return target
        try:
            parsed = json.loads(existing)
        except json.JSONDecodeError as exc:
            raise ValueError(f"refusing to replace malformed hook config {target}: {exc}") from exc
        if isinstance(parsed, dict) and parsed.get("version") == 1:
            print(f"Updating djobs-managed hook config in {target}")
        else:
            raise ValueError(f"refusing to replace unrecognized hook config {target}")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    print(f"Installed djobs automatic hooks in {target} (mode: {resolved_mode})")
    return target


def hook_doctor(root: Path | None = None) -> tuple[bool, str]:
    target = (root or Path.cwd()) / _HOOK_RELATIVE_PATH
    if not target.exists():
        return False, f"{target} not found — run 'djobs hook install'"
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"invalid JSON: {exc}"
    text = json.dumps(data, separators=(",", ":"))
    ok = "djobs hook pre" in text and "djobs hook session-start" in text
    return ok, f"installed at {target}" if ok else "hook commands are missing"


def print_hook_doctor(root: Path | None = None) -> None:
    ok, detail = hook_doctor(root)
    mark = "OK  " if ok else "FAIL"
    print(f"  [{mark}] automatic command hooks: {detail}")


def _cmd_pre() -> int:
    try:
        result = rewrite_pre_tool_payload(_read_payload())
    except Exception as exc:
        _debug(f"preToolUse processing failed: {exc}")
        result = {}
    print(json.dumps(result, separators=(",", ":")))
    return 0


def _cmd_session_start() -> int:
    try:
        result = session_start_context(_read_payload())
    except Exception as exc:
        _debug(f"sessionStart processing failed: {exc}")
        result = {}
    print(json.dumps(result, separators=(",", ":")))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="djobs hook", description="Automatic agent hook support")
    subparsers = parser.add_subparsers(dest="hook_command", required=True)

    install_parser = subparsers.add_parser("install", help="Install automatic repository hooks")
    install_parser.add_argument("--root", default=".", help="Repository root (default: current)")
    install_parser.add_argument("--mode", choices=sorted(_VALID_MODES), default="smart")
    install_parser.add_argument("--force", action="store_true")

    subparsers.add_parser("pre", help="Handle a preToolUse event from stdin")
    subparsers.add_parser("session-start", help="Handle a sessionStart event from stdin")

    run_parser = subparsers.add_parser("run", help="Run a hook-rewritten shell command")
    run_parser.add_argument("--payload", required=True, help="URL-safe encoded command envelope")

    doctor_parser = subparsers.add_parser("doctor", help="Check automatic hook installation")
    doctor_parser.add_argument("--root", default=".", help="Repository root (default: current)")

    args = parser.parse_args(argv)
    if args.hook_command == "install":
        install_hooks(Path(args.root), mode=args.mode, force=args.force)
        return 0
    if args.hook_command == "pre":
        return _cmd_pre()
    if args.hook_command == "session-start":
        return _cmd_session_start()
    if args.hook_command == "run":
        try:
            payload = _decode_envelope(args.payload)
        except Exception as exc:
            print(f"djobs hook: invalid --payload: {exc}", file=sys.stderr)
            return 2
        return run_wrapped_payload(payload)
    if args.hook_command == "doctor":
        print_hook_doctor(Path(args.root))
        ok, _ = hook_doctor(Path(args.root))
        return 0 if ok else 1
    return 2
