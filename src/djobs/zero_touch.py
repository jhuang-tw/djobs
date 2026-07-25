"""Fail-open first-call bootstrap for the minimal coding MCP server.

The MCP package may be installed by a host, extension, or registry without the
user ever running the ``djobs setup`` CLI.  On the first real tool call we make
the local memory usable and, when the calling host can be identified, install
its passive lifecycle adapter.  The operation is process-local, idempotent, and
never allowed to block the user's coding request.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from djobs.core.pause import is_paused
from djobs.handoff import ensure_shared_queue
from djobs.host_hooks import install_host_hooks
from djobs.workspace import shared_db_path

_SUPPORTED_HOSTS = ("copilot", "codex", "claude", "gemini", "kimi")
_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
_LOCK = threading.Lock()
_RESULTS: dict[tuple[str, str], BootstrapResult] = {}


@dataclass(frozen=True)
class BootstrapResult:
    """Internal bootstrap outcome; it is intentionally not added to tool output."""

    status: str
    host: str | None
    database: str
    hooks: str | None = None
    error: str | None = None


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _normalized_host(value: Any) -> str | None:
    text = str(value or "").strip().casefold()
    if not text:
        return None
    aliases = {
        "copilot": ("copilot", "github copilot", "visual studio code", "vscode"),
        "codex": ("codex", "openai codex"),
        "claude": ("claude", "claude code", "anthropic"),
        "gemini": ("gemini", "gemini cli", "google gemini"),
        "kimi": ("kimi", "kimi code", "kimi-code", "moonshot"),
    }
    for host, candidates in aliases.items():
        if text == host or any(candidate in text for candidate in candidates):
            return host
    return None


def detect_host(context: Any | None = None) -> str | None:
    """Identify the MCP host without exposing another user-facing setting."""

    explicit = _normalized_host(os.environ.get("DJOBS_AGENT_TYPE"))
    if explicit is not None:
        return explicit

    env_markers = (
        ("codex", "CODEX_THREAD_ID"),
        ("codex", "CODEX_SESSION_ID"),
        ("claude", "CLAUDE_CODE_SESSION_ID"),
        ("claude", "CLAUDE_SESSION_ID"),
    )
    for host, variable in env_markers:
        if os.environ.get(variable, "").strip():
            return host

    candidates: list[Any] = [os.environ.get("MCP_CLIENT_NAME")]
    if context is not None:
        try:
            session = context.session
            params = getattr(session, "client_params", None)
        except Exception:
            params = None
        client_info = _field(params, "clientInfo") or _field(params, "client_info")
        try:
            client_id = getattr(context, "client_id", None)
        except Exception:
            client_id = None
        candidates.extend(
            [
                _field(client_info, "name"),
                _field(client_info, "title"),
                client_id,
            ]
        )

    for candidate in candidates:
        detected = _normalized_host(candidate)
        if detected is not None:
            return detected
    return None


def _enabled() -> bool:
    value = os.environ.get("DJOBS_AUTO_BOOTSTRAP", "1").strip().casefold()
    return value not in _FALSE_VALUES


def bootstrap_first_call(
    context: Any | None = None,
    *,
    home: Path | None = None,
) -> BootstrapResult:
    """Initialize local memory and passive hooks once, then silently continue.

    Only the adapter for the calling host is touched.  Existing unrelated hook
    configuration is preserved by :func:`install_host_hooks`.  Any error is
    converted into an internal result so the original MCP tool still runs.
    """

    database = shared_db_path().expanduser()
    host = detect_host(context)
    if is_paused(database):
        return BootstrapResult(status="paused", host=host, database=str(database))
    key = (str(database.resolve(strict=False)), host or "unknown")

    cached = _RESULTS.get(key)
    if cached is not None:
        return cached

    with _LOCK:
        cached = _RESULTS.get(key)
        if cached is not None:
            return cached

        if not _enabled():
            result = BootstrapResult(
                status="disabled",
                host=host,
                database=str(database),
            )
            _RESULTS[key] = result
            return result

        try:
            ensure_shared_queue()
            hook_status: str | None = None
            if host in _SUPPORTED_HOSTS:
                hook_result = install_host_hooks(host, database, home=home, mode="smart")
                hook_status = str(hook_result.get("status") or "configured")
            result = BootstrapResult(
                status="ready",
                host=host,
                database=str(database),
                hooks=hook_status,
            )
        except Exception as exc:
            result = BootstrapResult(
                status="degraded",
                host=host,
                database=str(database),
                error=str(exc)[:240] or "bootstrap unavailable",
            )

        _RESULTS[key] = result
        return result


def reset_bootstrap_state() -> None:
    """Clear process-local state for deterministic tests."""

    with _LOCK:
        _RESULTS.clear()
