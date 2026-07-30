"""Persistence boundary for bounded fail-open diagnostics."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

_MAX_DIAGNOSTICS = 256


def _is_postgres(repo: Any) -> bool:
    return hasattr(repo, "_conn") and not hasattr(repo, "_connection")


def _fingerprint(component: str, error_type: str, message: str) -> str:
    normalized = " ".join(message.casefold().split())[:240]
    return hashlib.sha256(f"{component}\0{error_type}\0{normalized}".encode()).hexdigest()[:24]


def record(
    repo: Any,
    *,
    component: str,
    error_type: str,
    message: str,
    context: dict[str, Any] | None = None,
) -> None:
    """Upsert one redacted diagnostic and keep the table bounded."""

    now = datetime.now(timezone.utc)
    fingerprint = _fingerprint(component, error_type, message)
    context_json = json.dumps(
        context or {}, ensure_ascii=False, separators=(",", ":"), default=str
    )
    if _is_postgres(repo):
        with repo._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO djobs_diagnostics (
                    component, error_type, fingerprint, last_message, context_json,
                    occurrence_count, first_seen_at, last_seen_at
                ) VALUES (%s, %s, %s, %s, %s, 1, %s, %s)
                ON CONFLICT (component, fingerprint) DO UPDATE SET
                    error_type = EXCLUDED.error_type,
                    last_message = EXCLUDED.last_message,
                    context_json = EXCLUDED.context_json,
                    occurrence_count = djobs_diagnostics.occurrence_count + 1,
                    last_seen_at = EXCLUDED.last_seen_at
                """,
                (component, error_type, fingerprint, message, context_json, now, now),
            )
            cur.execute(
                """
                DELETE FROM djobs_diagnostics WHERE id IN (
                    SELECT id FROM djobs_diagnostics
                    ORDER BY last_seen_at DESC, id DESC OFFSET %s
                )
                """,
                (_MAX_DIAGNOSTICS,),
            )
        repo._conn.commit()
        return

    with repo.transaction(immediate=True) as transaction:
        transaction.execute(
            """
            INSERT INTO djobs_diagnostics (
                component, error_type, fingerprint, last_message, context_json,
                occurrence_count, first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(component, fingerprint) DO UPDATE SET
                error_type = excluded.error_type,
                last_message = excluded.last_message,
                context_json = excluded.context_json,
                occurrence_count = occurrence_count + 1,
                last_seen_at = excluded.last_seen_at
            """,
            (
                component,
                error_type,
                fingerprint,
                message,
                context_json,
                now.isoformat(),
                now.isoformat(),
            ),
        )
        transaction.execute(
            """
            DELETE FROM djobs_diagnostics WHERE id IN (
                SELECT id FROM djobs_diagnostics
                ORDER BY last_seen_at DESC, id DESC LIMIT -1 OFFSET ?
            )
            """,
            (_MAX_DIAGNOSTICS,),
        )


def list_recent(repo: Any, *, limit: int = 50) -> list[dict[str, Any]]:
    capped = max(1, min(int(limit), _MAX_DIAGNOSTICS))
    if _is_postgres(repo):
        with repo._conn.cursor() as cur:
            cur.execute(
                """
                SELECT component, error_type, fingerprint, last_message, context_json,
                       occurrence_count, first_seen_at, last_seen_at
                FROM djobs_diagnostics
                ORDER BY last_seen_at DESC, id DESC LIMIT %s
                """,
                (capped,),
            )
            rows = cur.fetchall()
    else:
        rows = repo.read_all(
            """
            SELECT component, error_type, fingerprint, last_message, context_json,
                   occurrence_count, first_seen_at, last_seen_at
            FROM djobs_diagnostics
            ORDER BY last_seen_at DESC, id DESC LIMIT ?
            """,
            (capped,),
        )
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["context"] = json.loads(str(item.pop("context_json") or "{}"))
        except json.JSONDecodeError:
            item["context"] = {}
        result.append(item)
    return result


def clear(repo: Any) -> int:
    if _is_postgres(repo):
        with repo._conn.cursor() as cur:
            cur.execute("DELETE FROM djobs_diagnostics")
            count = cur.rowcount
        repo._conn.commit()
        return int(count)
    return repo.execute_write("DELETE FROM djobs_diagnostics")
