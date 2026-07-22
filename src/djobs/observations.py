"""Client-neutral repository observations and Git snapshots.

Observations describe what happened in a repository.  They are deliberately
separate from durable jobs: recording a file/tool/session event must never
create, claim, complete, or release task ownership.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from datetime import datetime, timezone
from typing import Any

_MAX_SUMMARY = 500
_MAX_METADATA = 1000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_observations (
    id TEXT PRIMARY KEY,
    correlation_id TEXT NOT NULL,
    agent_type TEXT NOT NULL,
    session_id_hash TEXT NULL,
    event_type TEXT NOT NULL,
    tool_name TEXT NULL,
    summary TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_observations_scope_created
ON agent_observations (correlation_id, created_at DESC);

CREATE TABLE IF NOT EXISTS repository_snapshots (
    workspace_id TEXT PRIMARY KEY,
    digest TEXT NOT NULL,
    summary TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def clean(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").replace("\x00", "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def ensure_schema(repo: Any) -> None:
    with repo._lock:
        repo._connection.executescript(_SCHEMA)
        repo._connection.commit()


def record_observation(
    repo: Any,
    workspace: Any,
    agent: Any,
    event_type: str,
    summary: str,
    *,
    tool_name: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    ensure_schema(repo)
    session_hash = hashlib.sha256(agent.session_id.encode("utf-8")).hexdigest()[:16]
    encoded_metadata = clean(
        json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":"), default=str),
        _MAX_METADATA,
    )
    with repo._lock:
        repo._connection.execute(
            """
            INSERT INTO agent_observations (
                id, correlation_id, agent_type, session_id_hash, event_type,
                tool_name, summary, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                workspace.workspace_id,
                agent.agent_type,
                session_hash,
                clean(event_type, 80),
                clean(tool_name, 80) or None,
                clean(summary, _MAX_SUMMARY),
                encoded_metadata,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        repo._connection.commit()


def recent_observations(repo: Any, workspace: Any, limit: int = 6) -> list[dict[str, Any]]:
    ensure_schema(repo)
    placeholders = ",".join("?" for _ in workspace.correlation_ids)
    with repo._lock:
        rows = repo._connection.execute(
            f"""
            SELECT agent_type, event_type, tool_name, summary, created_at
            FROM agent_observations
            WHERE correlation_id IN ({placeholders})
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*workspace.correlation_ids, max(1, min(limit, 20))),
        ).fetchall()
    return [
        {
            "agent": row["agent_type"],
            "event": row["event_type"],
            "tool": row["tool_name"],
            "summary": row["summary"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def _git_state(root: str) -> tuple[str, str] | None:
    try:
        head = subprocess.run(
            ["git", "-C", root, "rev-parse", "--verify", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        status = subprocess.run(
            ["git", "-C", root, "status", "--porcelain=v1", "--untracked-files=normal"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if status.returncode != 0:
        return None
    head_text = head.stdout.strip() if head.returncode == 0 else "unborn"
    status_text = status.stdout.strip()
    digest = hashlib.sha256(f"{head_text}\n{status_text}".encode("utf-8")).hexdigest()
    lines = [line for line in status_text.splitlines() if line.strip()]
    if not lines:
        summary = f"HEAD {head_text[:12]}; working tree clean"
    else:
        shown = [clean(line, 180) for line in lines[:12]]
        extra = len(lines) - len(shown)
        summary = f"HEAD {head_text[:12]}; " + "; ".join(shown)
        if extra:
            summary += f"; +{extra} more"
    return digest, summary


def capture_repository_snapshot(repo: Any, workspace: Any, agent: Any) -> bool:
    """Record a Git delta when the actual working tree changed.

    This is the agent-agnostic fallback: changes made by a client with no hook
    support are still discovered the next time any adapter, MCP client, or
    sidecar inspects the repository.
    """

    state = _git_state(workspace.root)
    if state is None:
        return False
    digest, summary = state
    ensure_schema(repo)
    now = datetime.now(timezone.utc).isoformat()
    with repo._lock:
        previous = repo._connection.execute(
            "SELECT digest FROM repository_snapshots WHERE workspace_id = ?",
            (workspace.workspace_id,),
        ).fetchone()
        repo._connection.execute(
            """
            INSERT INTO repository_snapshots (workspace_id, digest, summary, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(workspace_id) DO UPDATE SET
                digest = excluded.digest,
                summary = excluded.summary,
                updated_at = excluded.updated_at
            """,
            (workspace.workspace_id, digest, summary, now),
        )
        repo._connection.commit()
    if previous is None or previous["digest"] == digest:
        return False
    record_observation(
        repo,
        workspace,
        agent,
        "repository_change",
        summary,
        metadata={"source": "git_snapshot", "stored_as_data": True},
    )
    return True
