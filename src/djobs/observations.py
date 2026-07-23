"""Client-neutral repository observations and Git snapshots.

Observations describe what happened in a repository. They are deliberately
separate from durable jobs: recording a file/tool/session event must never
create, claim, complete, or release task ownership.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_MAX_SUMMARY = 500
_MAX_METADATA = 1000
_MAX_OBSERVATIONS_PER_WORKSPACE = 1000
_HASH_CHUNK_SIZE = 64 * 1024
_MAX_UNTRACKED_HASH_BYTES = 1024 * 1024

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


def _metadata_json(metadata: dict[str, Any] | None) -> str:
    """Serialize bounded metadata without ever producing invalid JSON."""

    raw = json.dumps(
        metadata or {},
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    if len(raw) <= _MAX_METADATA:
        return raw
    preview_limit = max(80, _MAX_METADATA - 80)
    return json.dumps(
        {"truncated": True, "preview": clean(raw, preview_limit)},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def ensure_schema(repo: Any) -> None:
    with repo._lock:
        repo._connection.executescript(_SCHEMA)
        repo._connection.commit()


def _insert_observation(
    cursor: Any,
    workspace: Any,
    agent: Any,
    event_type: str,
    summary: str,
    *,
    tool_name: str | None = None,
    metadata: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> None:
    cursor.execute(
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
            hashlib.sha256(agent.session_id.encode("utf-8")).hexdigest()[:16],
            clean(event_type, 80),
            clean(tool_name, 80) or None,
            clean(summary, _MAX_SUMMARY),
            _metadata_json(metadata),
            created_at or datetime.now(timezone.utc).isoformat(),
        ),
    )
    cursor.execute(
        """
        DELETE FROM agent_observations
        WHERE correlation_id = ?
          AND id NOT IN (
              SELECT id
              FROM agent_observations
              WHERE correlation_id = ?
              ORDER BY created_at DESC, id DESC
              LIMIT ?
          )
        """,
        (
            workspace.workspace_id,
            workspace.workspace_id,
            _MAX_OBSERVATIONS_PER_WORKSPACE,
        ),
    )


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
    with repo._lock:
        cursor = repo._connection.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            _insert_observation(
                cursor,
                workspace,
                agent,
                event_type,
                summary,
                tool_name=tool_name,
                metadata=metadata,
            )
            repo._connection.commit()
        except Exception:
            repo._connection.rollback()
            raise


def recent_observations(repo: Any, workspace: Any, limit: int = 6) -> list[dict[str, Any]]:
    ensure_schema(repo)
    placeholders = ",".join("?" for _ in workspace.correlation_ids)
    with repo._lock:
        rows = repo._connection.execute(
            f"""
            SELECT agent_type, event_type, tool_name, summary, created_at
            FROM agent_observations
            WHERE correlation_id IN ({placeholders})
            ORDER BY created_at DESC, id DESC
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


def _hash_command(digest: Any, command: list[str]) -> bool:
    """Stream command output into a digest without retaining a potentially huge diff."""

    try:
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(
                command,
                stdout=output,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
            if result.returncode != 0:
                return False
            output.seek(0)
            while chunk := output.read(_HASH_CHUNK_SIZE):
                digest.update(chunk)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def _hash_file(digest: Any, path: Path) -> None:
    try:
        info = path.lstat()
    except OSError:
        digest.update(b"missing")
        return

    digest.update(f"{info.st_mode}:{info.st_size}:{info.st_mtime_ns}".encode("utf-8"))
    if stat.S_ISLNK(info.st_mode):
        try:
            digest.update(os.readlink(path).encode("utf-8", errors="surrogateescape"))
        except OSError:
            digest.update(b"broken-symlink")
        return
    if not stat.S_ISREG(info.st_mode):
        return

    try:
        with path.open("rb") as stream:
            if info.st_size <= _MAX_UNTRACKED_HASH_BYTES:
                while chunk := stream.read(_HASH_CHUNK_SIZE):
                    digest.update(chunk)
                return
            digest.update(stream.read(_HASH_CHUNK_SIZE))
            stream.seek(max(0, info.st_size - _HASH_CHUNK_SIZE))
            digest.update(stream.read(_HASH_CHUNK_SIZE))
    except OSError:
        digest.update(b"unreadable")


def _hash_untracked(digest: Any, root: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", root, "ls-files", "--others", "--exclude-standard", "-z"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False

    root_path = Path(root)
    for raw in sorted(item for item in result.stdout.split(b"\x00") if item):
        digest.update(raw)
        relative = os.fsdecode(raw)
        _hash_file(digest, root_path / relative)
    return True


def _git_state(root: str) -> tuple[str, str, bool] | None:
    try:
        head = subprocess.run(
            ["git", "-C", root, "rev-parse", "--verify", "HEAD"],
            capture_output=True,
            timeout=3,
            check=False,
        )
        status = subprocess.run(
            ["git", "-C", root, "status", "--porcelain=v1", "--untracked-files=normal"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if status.returncode != 0:
        return None

    head_bytes = head.stdout.strip() if head.returncode == 0 else b"unborn"
    head_text = os.fsdecode(head_bytes)
    status_text = status.stdout.decode("utf-8", errors="replace").strip()
    digest = hashlib.sha256()
    digest.update(head_bytes)
    digest.update(b"\x00")
    digest.update(status.stdout)
    tracked_ok = _hash_command(
        digest,
        ["git", "-C", root, "diff", "--no-ext-diff", "--binary", "--no-color"],
    )
    staged_ok = _hash_command(
        digest,
        ["git", "-C", root, "diff", "--cached", "--no-ext-diff", "--binary", "--no-color"],
    )
    untracked_ok = _hash_untracked(digest, root)
    if not (tracked_ok and staged_ok and untracked_ok):
        return None

    lines = [line for line in status_text.splitlines() if line.strip()]
    if not lines:
        summary = f"HEAD {head_text[:12]}; working tree clean"
    else:
        shown = [clean(line, 180) for line in lines[:12]]
        extra = len(lines) - len(shown)
        summary = f"HEAD {head_text[:12]}; " + "; ".join(shown)
        if extra:
            summary += f"; +{extra} more"
    return digest.hexdigest(), summary, bool(lines)


def capture_repository_snapshot(repo: Any, workspace: Any, agent: Any) -> bool:
    """Atomically record a Git delta when actual repository content changed.

    The digest includes tracked, staged, and untracked content, not only status
    letters. This is the agent-agnostic fallback for clients with no hooks.
    """

    state = _git_state(workspace.root)
    if state is None:
        return False
    digest, summary, dirty = state
    ensure_schema(repo)
    now = datetime.now(timezone.utc).isoformat()

    with repo._lock:
        cursor = repo._connection.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            previous = cursor.execute(
                "SELECT digest FROM repository_snapshots WHERE workspace_id = ?",
                (workspace.workspace_id,),
            ).fetchone()
            cursor.execute(
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
            changed = previous is None or previous["digest"] != digest
            should_record = changed and (previous is not None or dirty)
            if should_record:
                _insert_observation(
                    cursor,
                    workspace,
                    agent,
                    "repository_change",
                    summary,
                    metadata={"source": "git_snapshot", "stored_as_data": True},
                    created_at=now,
                )
            repo._connection.commit()
            return should_record
        except Exception:
            repo._connection.rollback()
            raise
