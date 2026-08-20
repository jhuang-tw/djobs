"""Generic MCP-only fallbacks for passive project-memory adoption.

Known coding hosts can install richer lifecycle adapters. Unknown MCP clients may
not have hooks at all, so a normal ``sync_workspace`` call also remembers the
current request *after* recovery. Agents can additionally preserve only important
cross-session progress, failures, decisions, or constraints through the existing
``memory`` MCP tool.
"""

from __future__ import annotations

import os
from typing import Any, Literal

from djobs.diagnostics import record_shared_failure
from djobs.handoff import _resolve
from djobs.lifecycle import automatic_memory_paused
from djobs.observations import clean, record_unique_session_observation
from djobs.privacy import redact_text

_NO_MEMORY_MARKERS = ("[djobs:no-memory]", "<djobs:no-memory>")
_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
MemoryNoteKind = Literal["progress", "failure", "decision", "constraint", "note"]
_NOTE_EVENT = {
    "progress": "agent_progress",
    "failure": "agent_failure",
    "decision": "agent_decision",
    "constraint": "agent_constraint",
    "note": "agent_note",
}


def _capture_enabled() -> bool:
    value = os.environ.get("DJOBS_CAPTURE_USER_INTENT", "1").strip().casefold()
    return value not in _FALSE_VALUES


def _bounded_text(value: str | None) -> str:
    return clean(redact_text(value or ""), 500)


def _suppressed(text: str) -> bool:
    return not text or any(marker in text.casefold() for marker in _NO_MEMORY_MARKERS)


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
    text = _bounded_text(query)
    if _suppressed(text):
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


def remember_agent_memory(
    summary: str | None,
    *,
    kind: MemoryNoteKind,
    roots: list[Any] | tuple[Any, ...] | None,
    cwd: str | None,
    agent_type: str | None,
) -> bool:
    """Persist one significant agent-authored fact for a future session.

    This is explicit but passive: it never creates or claims work. It exists for
    generic MCP clients whose tool results cannot be observed by djobs lifecycle
    hooks. Routine tool output should not be stored through this path.
    """

    if automatic_memory_paused():
        return False
    text = _bounded_text(summary)
    if _suppressed(text):
        return False
    event_type = _NOTE_EVENT[kind]
    try:
        workspace, agent, _queue, repo = _resolve(
            roots=roots,
            cwd=cwd,
            agent_type=agent_type or "mcp",
            session_id=None,
        )
        return record_unique_session_observation(
            repo,
            workspace,
            agent,
            event_type,
            text,
            metadata={
                "source": "mcp_memory",
                "note_kind": kind,
                "stored_as_data": True,
            },
        )
    except Exception as exc:
        record_shared_failure(
            "mcp_adoption.agent_memory",
            exc,
            context={"agent_type": agent_type or "unknown", "kind": kind},
        )
        return False
