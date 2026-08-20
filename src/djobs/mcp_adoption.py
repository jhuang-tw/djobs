"""Generic MCP-only fallback for passive project-memory adoption.

Known coding hosts can install richer lifecycle adapters. Unknown MCP clients may
not have hooks at all, so a normal ``sync_workspace`` call also remembers the
current request *after* recovery. This keeps the read-before-write invariant:
the current prompt can never rank as its own historical context.
"""

from __future__ import annotations

import os
from typing import Any

from djobs.diagnostics import record_shared_failure
from djobs.handoff import _resolve
from djobs.lifecycle import automatic_memory_paused
from djobs.observations import clean, record_unique_session_observation
from djobs.privacy import redact_text

_NO_MEMORY_MARKERS = ("[djobs:no-memory]", "<djobs:no-memory>")
_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}


def _capture_enabled() -> bool:
    value = os.environ.get("DJOBS_CAPTURE_USER_INTENT", "1").strip().casefold()
    return value not in _FALSE_VALUES


def remember_current_request(
    query: str | None,
    *,
    roots: list[Any] | tuple[Any, ...] | None,
    cwd: str | None,
    agent_type: str | None,
) -> None:
    """Store one bounded MCP request as passive memory, never task ownership.

    This is intentionally fail-open and returns no agent-facing payload. It is a
    fallback for MCP clients without lifecycle hooks; known hosts may record the
    same request through their adapter, and the per-session observation writer
    deduplicates identical intent.
    """

    if automatic_memory_paused() or not _capture_enabled():
        return
    text = clean(redact_text(query or ""), 500)
    if not text or any(marker in text.casefold() for marker in _NO_MEMORY_MARKERS):
        return
    try:
        workspace, agent, _queue, repo = _resolve(
            roots=roots,
            cwd=cwd,
            agent_type=agent_type or "mcp",
            session_id=None,
        )
        record_unique_session_observation(
            repo,
            workspace,
            agent,
            "user_intent",
            text,
            metadata={"source": "mcp_sync", "stored_as_data": True},
        )
    except Exception as exc:
        record_shared_failure(
            "mcp_adoption.user_intent",
            exc,
            context={"agent_type": agent_type or "unknown"},
        )
