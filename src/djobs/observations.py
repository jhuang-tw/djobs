"""Client-neutral repository observations and Git snapshots.

Observations describe what happened in a repository. They are deliberately
separate from durable jobs: recording a file/tool/session event must never
create, claim, complete, or release task ownership.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from djobs.storage.schema import (
    SQLITE_OBSERVATION_FTS_SCHEMA_SQL,
    SQLITE_OBSERVATION_SCHEMA_SQL,
)

_MAX_SUMMARY = 500
_MAX_METADATA = 1000
_MAX_OBSERVATIONS_PER_WORKSPACE = 1000
_MAX_CONTEXT_MARKERS_PER_WORKSPACE = 256
_HASH_CHUNK_SIZE = 64 * 1024
_MAX_UNTRACKED_HASH_BYTES = 1024 * 1024
_CONTEXT_INJECTED_EVENT = "context_injected"


def clean(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").replace("\x00", "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _session_hash(agent: Any) -> str:
    return hashlib.sha256(agent.session_id.encode("utf-8")).hexdigest()[:16]


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
    """Create observation storage and an optional SQLite FTS5 search index.

    Some embedded SQLite builds omit FTS5. Repository memory still works in
    that case through the bounded relevance fallback in :func:`search_observations`.
    """

    with repo._lock:
        repo._connection.executescript(SQLITE_OBSERVATION_SCHEMA_SQL)
        try:
            repo._connection.executescript(SQLITE_OBSERVATION_FTS_SCHEMA_SQL)
            repo._connection.execute(
                """
                INSERT INTO agent_observations_fts (
                    observation_id, correlation_id, event_type, tool_name, summary
                )
                SELECT o.id, o.correlation_id, o.event_type, COALESCE(o.tool_name, ''), o.summary
                FROM agent_observations AS o
                WHERE NOT EXISTS (
                    SELECT 1 FROM agent_observations_fts AS f
                    WHERE f.observation_id = o.id
                )
                """
            )
        except sqlite3.OperationalError:
            # FTS is an optimization, never a prerequisite for memory.
            pass
        repo._connection.commit()


def _prune_observations(cursor: Any, workspace_id: str) -> None:
    """Bound visible observations without evicting live injection markers."""

    cursor.execute(
        """
        DELETE FROM agent_observations
        WHERE correlation_id = ?
          AND event_type != ?
          AND id NOT IN (
              SELECT id
              FROM agent_observations
              WHERE correlation_id = ?
                AND event_type != ?
              ORDER BY created_at DESC, id DESC
              LIMIT ?
          )
        """,
        (
            workspace_id,
            _CONTEXT_INJECTED_EVENT,
            workspace_id,
            _CONTEXT_INJECTED_EVENT,
            _MAX_OBSERVATIONS_PER_WORKSPACE,
        ),
    )
    cursor.execute(
        """
        DELETE FROM agent_observations
        WHERE correlation_id = ?
          AND event_type = ?
          AND id NOT IN (
              SELECT id
              FROM agent_observations
              WHERE correlation_id = ?
                AND event_type = ?
              ORDER BY created_at DESC, id DESC
              LIMIT ?
          )
        """,
        (
            workspace_id,
            _CONTEXT_INJECTED_EVENT,
            workspace_id,
            _CONTEXT_INJECTED_EVENT,
            _MAX_CONTEXT_MARKERS_PER_WORKSPACE,
        ),
    )


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
            _session_hash(agent),
            clean(event_type, 80),
            clean(tool_name, 80) or None,
            clean(summary, _MAX_SUMMARY),
            _metadata_json(metadata),
            created_at or datetime.now(timezone.utc).isoformat(),
        ),
    )
    _prune_observations(cursor, workspace.workspace_id)


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


def record_unique_session_observation(
    repo: Any,
    workspace: Any,
    agent: Any,
    event_type: str,
    summary: str,
    *,
    tool_name: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Insert one exact observation at most once per client session.

    Prompt hooks can be delivered more than once by overlapping host adapters or
    resume paths. Deduplicating only exact event/summary pairs keeps meaningful
    repeated work while preventing identical user intent from filling memory.
    """

    ensure_schema(repo)
    session_hash = _session_hash(agent)
    placeholders = ",".join("?" for _ in workspace.correlation_ids)
    cleaned_summary = clean(summary, _MAX_SUMMARY)
    with repo._lock:
        cursor = repo._connection.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            existing = cursor.execute(
                f"""
                SELECT 1 FROM agent_observations
                WHERE correlation_id IN ({placeholders})
                  AND session_id_hash = ?
                  AND event_type = ?
                  AND summary = ?
                LIMIT 1
                """,
                (*workspace.correlation_ids, session_hash, clean(event_type, 80), cleaned_summary),
            ).fetchone()
            if existing is not None:
                repo._connection.commit()
                return False
            _insert_observation(
                cursor,
                workspace,
                agent,
                event_type,
                cleaned_summary,
                tool_name=tool_name,
                metadata=metadata,
            )
            repo._connection.commit()
            return True
        except Exception:
            repo._connection.rollback()
            raise


def reset_context_injection(repo: Any, workspace: Any, agent: Any) -> None:
    """Allow one fresh prompt-context injection after a session start/resume."""

    ensure_schema(repo)
    with repo._lock:
        repo._connection.execute(
            """
            DELETE FROM agent_observations
            WHERE correlation_id = ?
              AND agent_type = ?
              AND session_id_hash = ?
              AND event_type = ?
            """,
            (
                workspace.workspace_id,
                agent.agent_type,
                _session_hash(agent),
                _CONTEXT_INJECTED_EVENT,
            ),
        )
        repo._connection.commit()


def claim_context_injection(
    repo: Any,
    workspace: Any,
    agent: Any,
    *,
    context_key: str | None = None,
) -> bool:
    """Atomically deduplicate repository-context injection.

    With no key this preserves the original once-per-session behavior used by
    startup-only clients. Prompt hooks pass the current request, allowing one
    relevant injection per distinct request while suppressing duplicate delivery.
    """

    ensure_schema(repo)
    now = datetime.now(timezone.utc).isoformat()
    key_hash = (
        hashlib.sha256(clean(context_key, 500).encode("utf-8")).hexdigest()[:16]
        if context_key
        else "session"
    )
    marker_summary = f"Read-only repository context injected ({key_hash})."
    with repo._lock:
        cursor = repo._connection.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            existing = cursor.execute(
                """
                SELECT 1 FROM agent_observations
                WHERE correlation_id = ?
                  AND agent_type = ?
                  AND session_id_hash = ?
                  AND event_type = ?
                  AND summary = ?
                LIMIT 1
                """,
                (
                    workspace.workspace_id,
                    agent.agent_type,
                    _session_hash(agent),
                    _CONTEXT_INJECTED_EVENT,
                    marker_summary,
                ),
            ).fetchone()
            if existing is not None:
                repo._connection.commit()
                return False
            _insert_observation(
                cursor,
                workspace,
                agent,
                _CONTEXT_INJECTED_EVENT,
                marker_summary,
                metadata={
                    "internal": True,
                    "context_key_hash": key_hash,
                    "stored_as_data": True,
                },
                created_at=now,
            )
            repo._connection.commit()
            return True
        except Exception:
            repo._connection.rollback()
            raise


def _row_to_observation(row: Any, *, score: float | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": row["id"],
        "agent": row["agent_type"],
        "event": row["event_type"],
        "tool": row["tool_name"],
        "summary": row["summary"],
        "created_at": row["created_at"],
    }
    if score is not None:
        item["score"] = round(float(score), 4)
    return item


def recent_observations(repo: Any, workspace: Any, limit: int = 6) -> list[dict[str, Any]]:
    ensure_schema(repo)
    placeholders = ",".join("?" for _ in workspace.correlation_ids)
    with repo._lock:
        rows = repo._connection.execute(
            f"""
            SELECT id, agent_type, event_type, tool_name, summary, created_at
            FROM agent_observations
            WHERE correlation_id IN ({placeholders})
              AND event_type != ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (
                *workspace.correlation_ids,
                _CONTEXT_INJECTED_EVENT,
                max(1, min(limit, 20)),
            ),
        ).fetchall()
    return [_row_to_observation(row) for row in rows]


def _search_terms(query: str) -> list[str]:
    terms = [item.strip("._-/") for item in re.findall(r"[\w./-]+", query, re.UNICODE)]
    return [item for item in terms if len(item) >= 2][:12]


def _fts_query(query: str) -> str:
    terms = _search_terms(query)
    return " OR ".join(f'"{term.replace(chr(34), "")}"' for term in terms)


def search_observations(
    repo: Any,
    workspace: Any,
    query: str,
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Return repository memories ranked for the user's current request.

    SQLite FTS5 is used when available. A deterministic token-overlap fallback
    keeps the feature portable and makes non-English substring matches useful.
    """

    ensure_schema(repo)
    cleaned_query = clean(query, 500)
    if not cleaned_query:
        return recent_observations(repo, workspace, limit=limit)
    capped = max(1, min(limit, 20))
    placeholders = ",".join("?" for _ in workspace.correlation_ids)
    fts = _fts_query(cleaned_query)
    if fts:
        try:
            with repo._lock:
                rows = repo._connection.execute(
                    f"""
                    SELECT o.id, o.agent_type, o.event_type, o.tool_name, o.summary,
                           o.created_at,
                           bm25(agent_observations_fts, 0.0, 0.0, 1.0, 1.0, 2.5)
                             + CASE o.event_type
                                 WHEN 'session_capsule' THEN -2.0
                                 WHEN 'user_intent' THEN -1.0
                                 WHEN 'tool_failure' THEN -0.4
                                 ELSE 0.0
                               END AS relevance
                    FROM agent_observations_fts
                    JOIN agent_observations AS o
                      ON o.id = agent_observations_fts.observation_id
                    WHERE agent_observations_fts MATCH ?
                      AND o.correlation_id IN ({placeholders})
                      AND o.event_type != ?
                    ORDER BY relevance ASC, o.created_at DESC
                    LIMIT ?
                    """,
                    (fts, *workspace.correlation_ids, _CONTEXT_INJECTED_EVENT, capped),
                ).fetchall()
            if rows:
                return [_row_to_observation(row, score=-float(row["relevance"])) for row in rows]
        except sqlite3.OperationalError:
            pass

    # Fallback: retrieve a bounded candidate set and score exact phrase and token overlap.
    with repo._lock:
        rows = repo._connection.execute(
            f"""
            SELECT id, agent_type, event_type, tool_name, summary, created_at
            FROM agent_observations
            WHERE correlation_id IN ({placeholders})
              AND event_type != ?
            ORDER BY created_at DESC, id DESC
            LIMIT 200
            """,
            (*workspace.correlation_ids, _CONTEXT_INJECTED_EVENT),
        ).fetchall()
    needle = cleaned_query.casefold()
    terms = [term.casefold() for term in _search_terms(cleaned_query)]
    scored: list[tuple[float, Any]] = []
    for index, row in enumerate(rows):
        haystack = f"{row['event_type']} {row['tool_name'] or ''} {row['summary']}".casefold()
        overlap = sum(1 for term in terms if term in haystack)
        exact = 4.0 if needle in haystack else 0.0
        type_boost = {
            "session_capsule": 2.0,
            "user_intent": 1.4,
            "tool_failure": 0.8,
            "repository_change": 0.4,
        }.get(str(row["event_type"]), 0.0)
        recency = max(0.0, 1.0 - index / 200)
        score = exact + float(overlap) + type_boost + recency
        if score > recency or not terms:
            scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [_row_to_observation(row, score=score) for score, row in scored[:capped]]


def session_observations(repo: Any, workspace: Any, agent: Any, limit: int = 40) -> list[Any]:
    """Return meaningful events since the latest capsule in this exact session."""

    ensure_schema(repo)
    placeholders = ",".join("?" for _ in workspace.correlation_ids)
    session_hash = _session_hash(agent)
    with repo._lock:
        capsule = repo._connection.execute(
            f"""
            SELECT created_at FROM agent_observations
            WHERE correlation_id IN ({placeholders})
              AND session_id_hash = ?
              AND event_type = 'session_capsule'
            ORDER BY created_at DESC, id DESC LIMIT 1
            """,
            (*workspace.correlation_ids, session_hash),
        ).fetchone()
        after = str(capsule["created_at"]) if capsule is not None else ""
        rows = repo._connection.execute(
            f"""
            SELECT id, agent_type, event_type, tool_name, summary, metadata_json, created_at
            FROM agent_observations
            WHERE correlation_id IN ({placeholders})
              AND session_id_hash = ?
              AND event_type IN ('user_intent', 'tool_result', 'tool_failure', 'repository_change')
              AND created_at > ?
            ORDER BY created_at ASC, id ASC
            LIMIT ?
            """,
            (*workspace.correlation_ids, session_hash, after, max(1, min(limit, 100))),
        ).fetchall()
    return list(rows)


def record_session_capsule(
    repo: Any,
    workspace: Any,
    agent: Any,
    *,
    reason: str,
    next_hint: str | None = None,
) -> bool:
    """Store one deterministic, structured summary of the current session.

    This is intentionally model-free: it groups the user's latest intent,
    completed and failed tool evidence, and Git state without uploading content.
    """

    rows = session_observations(repo, workspace, agent)
    if not rows:
        return False
    intents = [clean(row["summary"], 320) for row in rows if row["event_type"] == "user_intent"]
    progress = [
        clean(row["summary"], 220)
        for row in rows
        if row["event_type"] in {"tool_result", "repository_change"}
    ]
    failures = [clean(row["summary"], 220) for row in rows if row["event_type"] == "tool_failure"]
    goal = intents[-1] if intents else "Continue the repository work recorded in this session."
    parts = [f"Goal: {goal}"]
    if progress:
        parts.append("Progress: " + " | ".join(progress[-3:]))
    if failures:
        parts.append("Failed: " + " | ".join(failures[-2:]))
    if next_hint:
        parts.append("Next: " + clean(next_hint, 240))
    summary = clean(" || ".join(parts), _MAX_SUMMARY)
    record_observation(
        repo,
        workspace,
        agent,
        "session_capsule",
        summary,
        metadata={
            "reason": clean(reason, 80),
            "goal": goal,
            "progress": progress[-5:],
            "failures": failures[-3:],
            "next": clean(next_hint, 240) if next_hint else None,
            "source_event_ids": [str(row["id"]) for row in rows[-20:]],
            "stored_as_data": True,
        },
    )
    return True


def forget_observation(repo: Any, workspace: Any, memory_id: str) -> bool:
    """Delete one observation only when it belongs to the current repository."""

    ensure_schema(repo)
    placeholders = ",".join("?" for _ in workspace.correlation_ids)
    with repo._lock:
        cursor = repo._connection.execute(
            f"DELETE FROM agent_observations WHERE id = ? AND correlation_id IN ({placeholders})",
            (memory_id, *workspace.correlation_ids),
        )
        repo._connection.commit()
        return cursor.rowcount == 1


def clear_workspace_memory(repo: Any, workspace: Any) -> int:
    """Clear passive observations and Git snapshot, preserving explicit tasks."""

    ensure_schema(repo)
    placeholders = ",".join("?" for _ in workspace.correlation_ids)
    with repo._lock:
        cursor = repo._connection.execute(
            f"DELETE FROM agent_observations WHERE correlation_id IN ({placeholders})",
            workspace.correlation_ids,
        )
        count = int(cursor.rowcount)
        repo._connection.execute(
            "DELETE FROM repository_snapshots WHERE workspace_id = ?",
            (workspace.workspace_id,),
        )
        repo._connection.commit()
    return count


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

    digest.update(f"{info.st_mode}:{info.st_size}:{info.st_mtime_ns}".encode())
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
    lines = [line for line in status_text.splitlines() if line.strip()]
    if lines:
        tracked_ok = _hash_command(
            digest,
            [
                "git",
                "-C",
                root,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--binary",
                "--no-color",
            ],
        )
        staged_ok = _hash_command(
            digest,
            [
                "git",
                "-C",
                root,
                "diff",
                "--cached",
                "--no-ext-diff",
                "--no-textconv",
                "--binary",
                "--no-color",
            ],
        )
        untracked_ok = _hash_untracked(digest, root)
        if not (tracked_ok and staged_ok and untracked_ok):
            return None

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
