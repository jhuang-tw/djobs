"""Storage adapters for passive repository memory.

All SQL for observations, context markers, capsules, snapshots, retention, and
memory diagnostics lives in this module. Service layers receive plain mappings
and never access a repository's private database handle.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from djobs.storage.schema import (
    POSTGRES_OBSERVATION_SCHEMA_SQL,
    SQLITE_OBSERVATION_FTS_SCHEMA_SQL,
    SQLITE_OBSERVATION_SCHEMA_SQL,
)


@runtime_checkable
class MemoryRepository(Protocol):
    def ensure_schema(self) -> None: ...

    def insert_observation(
        self,
        record: dict[str, Any],
        *,
        marker_event: str,
        max_observations: int,
        max_markers: int,
    ) -> str: ...

    def insert_unique_observation(
        self,
        record: dict[str, Any],
        *,
        scopes: tuple[str, ...],
        marker_event: str,
        max_observations: int,
        max_markers: int,
    ) -> bool: ...

    def reset_context_marker(
        self,
        *,
        scope: str,
        agent_type: str,
        session_hash: str,
        marker_event: str,
    ) -> None: ...

    def claim_context_marker(
        self,
        record: dict[str, Any],
        *,
        marker_event: str,
        max_observations: int,
        max_markers: int,
    ) -> bool: ...

    def recent_rows(
        self,
        *,
        scopes: tuple[str, ...],
        marker_event: str,
        limit: int,
    ) -> list[dict[str, Any]]: ...

    def fts_rows(
        self,
        *,
        scopes: tuple[str, ...],
        query: str,
        marker_event: str,
        limit: int,
    ) -> list[dict[str, Any]]: ...

    def scan_rows(
        self,
        *,
        scopes: tuple[str, ...],
        marker_event: str,
        limit: int,
    ) -> list[dict[str, Any]]: ...

    def observation_metadata(self, *, memory_id: str, scopes: tuple[str, ...]) -> str | None: ...

    def update_observation_metadata(self, *, memory_id: str, metadata_json: str) -> bool: ...

    def session_rows(
        self,
        *,
        scopes: tuple[str, ...],
        session_hash: str,
        limit: int,
    ) -> list[dict[str, Any]]: ...

    def forget(self, *, memory_id: str, scopes: tuple[str, ...]) -> bool: ...
    def clear(self, *, scopes: tuple[str, ...], checkout_id: str) -> int: ...

    def upsert_snapshot(
        self,
        *,
        checkout_id: str,
        digest: str,
        summary: str,
        updated_at: str,
        observation: dict[str, Any] | None,
        record_initial: bool,
        marker_event: str,
        max_observations: int,
        max_markers: int,
    ) -> bool: ...

    def stats(self, *, scopes: tuple[str, ...]) -> dict[str, Any]: ...

    def compact(
        self,
        *,
        scopes: tuple[str, ...],
        keep_recent: int,
        dry_run: bool,
    ) -> dict[str, int]: ...


@dataclass(slots=True)
class SQLiteMemoryRepository:
    repo: Any

    def ensure_schema(self) -> None:
        if getattr(self.repo, "_memory_schema_ready", False):
            return
        with self.repo._lock:
            if getattr(self.repo, "_memory_schema_ready", False):
                return
            self.repo._connection.executescript(SQLITE_OBSERVATION_SCHEMA_SQL)
            try:
                self.repo._connection.executescript(SQLITE_OBSERVATION_FTS_SCHEMA_SQL)
                self.repo._connection.execute(
                    """
                    INSERT INTO agent_observations_fts (
                        observation_id, correlation_id, event_type, tool_name, summary
                    )
                    SELECT o.id, o.correlation_id, o.event_type,
                           COALESCE(o.tool_name, ''), o.summary
                    FROM agent_observations AS o
                    WHERE NOT EXISTS (
                        SELECT 1 FROM agent_observations_fts AS f
                        WHERE f.observation_id = o.id
                    )
                    """
                )
            except sqlite3.OperationalError:
                pass
            self.repo._connection.commit()
            self.repo._memory_schema_ready = True

    @staticmethod
    def _placeholders(values: tuple[str, ...]) -> str:
        return ",".join("?" for _ in values)

    @staticmethod
    def _insert(cursor: Any, record: dict[str, Any]) -> None:
        cursor.execute(
            """
            INSERT INTO agent_observations (
                id, correlation_id, agent_type, session_id_hash, event_type,
                tool_name, summary, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["id"],
                record["correlation_id"],
                record["agent_type"],
                record.get("session_id_hash"),
                record["event_type"],
                record.get("tool_name"),
                record["summary"],
                record["metadata_json"],
                record["created_at"],
            ),
        )

    @staticmethod
    def _prune(
        cursor: Any,
        *,
        scope: str,
        marker_event: str,
        max_observations: int,
        max_markers: int,
    ) -> None:
        cursor.execute(
            """
            DELETE FROM agent_observations
            WHERE correlation_id = ? AND event_type != ?
              AND id NOT IN (
                  SELECT id FROM agent_observations
                  WHERE correlation_id = ? AND event_type != ?
                  ORDER BY created_at DESC, id DESC LIMIT ?
              )
            """,
            (scope, marker_event, scope, marker_event, max_observations),
        )
        cursor.execute(
            """
            DELETE FROM agent_observations
            WHERE correlation_id = ? AND event_type = ?
              AND id NOT IN (
                  SELECT id FROM agent_observations
                  WHERE correlation_id = ? AND event_type = ?
                  ORDER BY created_at DESC, id DESC LIMIT ?
              )
            """,
            (scope, marker_event, scope, marker_event, max_markers),
        )

    def insert_observation(
        self,
        record: dict[str, Any],
        *,
        marker_event: str,
        max_observations: int,
        max_markers: int,
    ) -> str:
        self.ensure_schema()
        with self.repo.transaction(immediate=True) as transaction:
            self._insert(transaction, record)
            self._prune(
                transaction,
                scope=str(record["correlation_id"]),
                marker_event=marker_event,
                max_observations=max_observations,
                max_markers=max_markers,
            )
        return str(record["id"])

    def insert_unique_observation(
        self,
        record: dict[str, Any],
        *,
        scopes: tuple[str, ...],
        marker_event: str,
        max_observations: int,
        max_markers: int,
    ) -> bool:
        self.ensure_schema()
        placeholders = self._placeholders(scopes)
        with self.repo.transaction(immediate=True) as transaction:
            existing = transaction.execute(
                f"""
                SELECT 1 FROM agent_observations
                WHERE correlation_id IN ({placeholders})
                  AND session_id_hash = ? AND event_type = ? AND summary = ?
                LIMIT 1
                """,
                (
                    *scopes,
                    record.get("session_id_hash"),
                    record["event_type"],
                    record["summary"],
                ),
            ).fetchone()
            if existing is not None:
                return False
            self._insert(transaction, record)
            self._prune(
                transaction,
                scope=str(record["correlation_id"]),
                marker_event=marker_event,
                max_observations=max_observations,
                max_markers=max_markers,
            )
            return True

    def reset_context_marker(
        self,
        *,
        scope: str,
        agent_type: str,
        session_hash: str,
        marker_event: str,
    ) -> None:
        self.ensure_schema()
        self.repo.execute_write(
            """
            DELETE FROM agent_observations
            WHERE correlation_id = ? AND agent_type = ? AND session_id_hash = ?
              AND event_type = ?
            """,
            (scope, agent_type, session_hash, marker_event),
        )

    def claim_context_marker(
        self,
        record: dict[str, Any],
        *,
        marker_event: str,
        max_observations: int,
        max_markers: int,
    ) -> bool:
        self.ensure_schema()
        with self.repo.transaction(immediate=True) as transaction:
            existing = transaction.execute(
                """
                SELECT 1 FROM agent_observations
                WHERE correlation_id = ? AND agent_type = ? AND session_id_hash = ?
                  AND event_type = ? AND summary = ? LIMIT 1
                """,
                (
                    record["correlation_id"],
                    record["agent_type"],
                    record.get("session_id_hash"),
                    marker_event,
                    record["summary"],
                ),
            ).fetchone()
            if existing is not None:
                return False
            self._insert(transaction, record)
            self._prune(
                transaction,
                scope=str(record["correlation_id"]),
                marker_event=marker_event,
                max_observations=max_observations,
                max_markers=max_markers,
            )
            return True

    def recent_rows(
        self,
        *,
        scopes: tuple[str, ...],
        marker_event: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        self.ensure_schema()
        placeholders = self._placeholders(scopes)
        with self.repo._lock:
            rows = self.repo._connection.execute(
                f"""
                SELECT id, agent_type, event_type, tool_name, summary,
                       metadata_json, created_at
                FROM agent_observations
                WHERE correlation_id IN ({placeholders}) AND event_type != ?
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                (*scopes, marker_event, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def fts_rows(
        self,
        *,
        scopes: tuple[str, ...],
        query: str,
        marker_event: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        self.ensure_schema()
        placeholders = self._placeholders(scopes)
        try:
            with self.repo._lock:
                rows = self.repo._connection.execute(
                    f"""
                    SELECT o.id, o.agent_type, o.event_type, o.tool_name, o.summary,
                           o.metadata_json, o.created_at,
                           bm25(agent_observations_fts, 0.0, 0.0, 1.0, 1.0, 2.5)
                             + CASE o.event_type
                                 WHEN 'session_capsule' THEN -2.0
                                 WHEN 'user_intent' THEN -1.0
                                 WHEN 'tool_failure' THEN -0.4
                                 ELSE 0.0 END AS relevance
                    FROM agent_observations_fts
                    JOIN agent_observations AS o
                      ON o.id = agent_observations_fts.observation_id
                    WHERE agent_observations_fts MATCH ?
                      AND o.correlation_id IN ({placeholders}) AND o.event_type != ?
                    ORDER BY relevance ASC, o.created_at DESC LIMIT ?
                    """,
                    (query, *scopes, marker_event, limit),
                ).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.OperationalError:
            return []

    def scan_rows(
        self,
        *,
        scopes: tuple[str, ...],
        marker_event: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        return self.recent_rows(scopes=scopes, marker_event=marker_event, limit=limit)

    def observation_metadata(self, *, memory_id: str, scopes: tuple[str, ...]) -> str | None:
        self.ensure_schema()
        placeholders = self._placeholders(scopes)
        with self.repo._lock:
            row = self.repo._connection.execute(
                f"SELECT metadata_json FROM agent_observations "
                f"WHERE id = ? AND correlation_id IN ({placeholders})",
                (memory_id, *scopes),
            ).fetchone()
        return None if row is None else str(row["metadata_json"])

    def update_observation_metadata(self, *, memory_id: str, metadata_json: str) -> bool:
        self.ensure_schema()
        return (
            self.repo.execute_write(
                "UPDATE agent_observations SET metadata_json = ? WHERE id = ?",
                (metadata_json, memory_id),
            )
            == 1
        )

    def session_rows(
        self,
        *,
        scopes: tuple[str, ...],
        session_hash: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        self.ensure_schema()
        placeholders = self._placeholders(scopes)
        with self.repo._lock:
            capsule = self.repo._connection.execute(
                f"""
                SELECT created_at FROM agent_observations
                WHERE correlation_id IN ({placeholders}) AND session_id_hash = ?
                  AND event_type = 'session_capsule'
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (*scopes, session_hash),
            ).fetchone()
            after = str(capsule["created_at"]) if capsule is not None else ""
            rows = self.repo._connection.execute(
                f"""
                SELECT id, agent_type, event_type, tool_name, summary,
                       metadata_json, created_at
                FROM agent_observations
                WHERE correlation_id IN ({placeholders}) AND session_id_hash = ?
                  AND event_type IN (
                      'user_intent', 'tool_result', 'tool_failure', 'repository_change'
                  )
                  AND created_at > ?
                ORDER BY created_at ASC, id ASC LIMIT ?
                """,
                (*scopes, session_hash, after, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def forget(self, *, memory_id: str, scopes: tuple[str, ...]) -> bool:
        self.ensure_schema()
        placeholders = self._placeholders(scopes)
        return (
            self.repo.execute_write(
                f"DELETE FROM agent_observations "
                f"WHERE id = ? AND correlation_id IN ({placeholders})",
                (memory_id, *scopes),
            )
            == 1
        )

    def clear(self, *, scopes: tuple[str, ...], checkout_id: str) -> int:
        self.ensure_schema()
        placeholders = self._placeholders(scopes)
        with self.repo.transaction(immediate=True) as transaction:
            cursor = transaction.execute(
                f"DELETE FROM agent_observations WHERE correlation_id IN ({placeholders})",
                scopes,
            )
            count = int(cursor.rowcount)
            transaction.execute(
                "DELETE FROM repository_snapshots WHERE workspace_id = ?", (checkout_id,)
            )
        return count

    def upsert_snapshot(
        self,
        *,
        checkout_id: str,
        digest: str,
        summary: str,
        updated_at: str,
        observation: dict[str, Any] | None,
        record_initial: bool,
        marker_event: str,
        max_observations: int,
        max_markers: int,
    ) -> bool:
        self.ensure_schema()
        with self.repo.transaction(immediate=True) as transaction:
            previous = transaction.execute(
                "SELECT digest FROM repository_snapshots WHERE workspace_id = ?",
                (checkout_id,),
            ).fetchone()
            transaction.execute(
                """
                INSERT INTO repository_snapshots (workspace_id, digest, summary, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(workspace_id) DO UPDATE SET
                    digest = excluded.digest,
                    summary = excluded.summary,
                    updated_at = excluded.updated_at
                """,
                (checkout_id, digest, summary, updated_at),
            )
            changed = previous is None or str(previous["digest"]) != digest
            should_record = changed and (previous is not None or record_initial)
            if observation is not None and should_record:
                self._insert(transaction, observation)
                self._prune(
                    transaction,
                    scope=str(observation["correlation_id"]),
                    marker_event=marker_event,
                    max_observations=max_observations,
                    max_markers=max_markers,
                )
            return should_record

    def stats(self, *, scopes: tuple[str, ...]) -> dict[str, Any]:
        self.ensure_schema()
        placeholders = self._placeholders(scopes)
        with self.repo._lock:
            rows = self.repo._connection.execute(
                f"""
                SELECT event_type, metadata_json, COUNT(*) AS count
                FROM agent_observations
                WHERE correlation_id IN ({placeholders})
                GROUP BY event_type, metadata_json
                """,
                scopes,
            ).fetchall()
            total = self.repo._connection.execute(
                f"SELECT COUNT(*) AS count FROM agent_observations "
                f"WHERE correlation_id IN ({placeholders})",
                scopes,
            ).fetchone()
        return {"total": int(total["count"] if total else 0), "rows": [dict(row) for row in rows]}

    def compact(
        self,
        *,
        scopes: tuple[str, ...],
        keep_recent: int,
        dry_run: bool,
    ) -> dict[str, int]:
        self.ensure_schema()
        placeholders = self._placeholders(scopes)
        keep = max(1, int(keep_recent))
        with self.repo.transaction(immediate=not dry_run) as transaction:
            duplicate_rows = transaction.execute(
                f"""
                WITH ranked AS (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY correlation_id, event_type, summary
                               ORDER BY created_at DESC, id DESC
                           ) AS duplicate_rank
                    FROM agent_observations
                    WHERE correlation_id IN ({placeholders})
                      AND event_type NOT IN ('user_intent', 'context_injected')
                )
                SELECT id FROM ranked WHERE duplicate_rank > 1
                """,
                scopes,
            ).fetchall()
            superseded_rows = transaction.execute(
                f"""
                WITH inactive AS (
                    SELECT id,
                           ROW_NUMBER() OVER (ORDER BY created_at DESC, id DESC) AS inactive_rank
                    FROM agent_observations
                    WHERE correlation_id IN ({placeholders})
                      AND (metadata_json LIKE '%"memory_status":"resolved"%'
                           OR metadata_json LIKE '%"memory_status":"superseded"%'
                           OR metadata_json LIKE '%"memory_status":"stale"%'
                           OR metadata_json LIKE '%"memory_status":"contradicted"%')
                )
                SELECT id FROM inactive WHERE inactive_rank > ?
                """,
                (*scopes, keep),
            ).fetchall()
            duplicate_ids = {str(row["id"]) for row in duplicate_rows}
            inactive_ids = {str(row["id"]) for row in superseded_rows}
            selected = sorted(duplicate_ids | inactive_ids)
            if selected and not dry_run:
                delete_ph = ",".join("?" for _ in selected)
                transaction.execute(
                    f"DELETE FROM agent_observations WHERE id IN ({delete_ph})", selected
                )
        return {
            "duplicates": len(duplicate_ids),
            "inactive": len(inactive_ids),
            "total": len(selected),
        }


@dataclass(slots=True)
class PostgresMemoryRepository:
    repo: Any

    def ensure_schema(self) -> None:
        if getattr(self.repo, "_memory_schema_ready", False):
            return
        with self.repo._conn.cursor() as cur:
            cur.execute(POSTGRES_OBSERVATION_SCHEMA_SQL)
        self.repo._conn.commit()
        self.repo._memory_schema_ready = True

    @staticmethod
    def _placeholders(values: tuple[str, ...]) -> str:
        return ",".join("%s" for _ in values)

    @staticmethod
    def _insert(cur: Any, record: dict[str, Any]) -> None:
        cur.execute(
            """
            INSERT INTO agent_observations (
                id, correlation_id, agent_type, session_id_hash, event_type,
                tool_name, summary, metadata_json, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record["id"],
                record["correlation_id"],
                record["agent_type"],
                record.get("session_id_hash"),
                record["event_type"],
                record.get("tool_name"),
                record["summary"],
                record["metadata_json"],
                record["created_at"],
            ),
        )

    @staticmethod
    def _prune(
        cur: Any,
        *,
        scope: str,
        marker_event: str,
        max_observations: int,
        max_markers: int,
    ) -> None:
        cur.execute(
            """
            DELETE FROM agent_observations WHERE id IN (
                SELECT id FROM agent_observations
                WHERE correlation_id = %s AND event_type != %s
                ORDER BY created_at DESC, id DESC OFFSET %s
            )
            """,
            (scope, marker_event, max_observations),
        )
        cur.execute(
            """
            DELETE FROM agent_observations WHERE id IN (
                SELECT id FROM agent_observations
                WHERE correlation_id = %s AND event_type = %s
                ORDER BY created_at DESC, id DESC OFFSET %s
            )
            """,
            (scope, marker_event, max_markers),
        )

    def insert_observation(
        self, record: dict[str, Any], *, marker_event: str, max_observations: int, max_markers: int
    ) -> str:
        self.ensure_schema()
        with self.repo._conn.cursor() as cur:
            self._insert(cur, record)
            self._prune(
                cur,
                scope=str(record["correlation_id"]),
                marker_event=marker_event,
                max_observations=max_observations,
                max_markers=max_markers,
            )
        self.repo._conn.commit()
        return str(record["id"])

    def insert_unique_observation(
        self,
        record: dict[str, Any],
        *,
        scopes: tuple[str, ...],
        marker_event: str,
        max_observations: int,
        max_markers: int,
    ) -> bool:
        self.ensure_schema()
        ph = self._placeholders(scopes)
        lock_key = "\x1f".join(
            (
                *sorted(scopes),
                str(record.get("session_id_hash") or ""),
                str(record["event_type"]),
                str(record["summary"]),
            )
        )
        with self.repo._conn.cursor() as cur:
            # Serialize equivalent dedupe checks across concurrent writers. The
            # transaction-scoped lock is released automatically at commit or
            # rollback and keeps the check-then-insert operation deterministic.
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (lock_key,))
            cur.execute(
                f"SELECT 1 FROM agent_observations WHERE correlation_id IN ({ph}) "
                "AND session_id_hash = %s AND event_type = %s AND summary = %s LIMIT 1",
                (*scopes, record.get("session_id_hash"), record["event_type"], record["summary"]),
            )
            if cur.fetchone() is not None:
                self.repo._conn.commit()
                return False
            self._insert(cur, record)
            self._prune(
                cur,
                scope=str(record["correlation_id"]),
                marker_event=marker_event,
                max_observations=max_observations,
                max_markers=max_markers,
            )
        self.repo._conn.commit()
        return True

    def reset_context_marker(
        self, *, scope: str, agent_type: str, session_hash: str, marker_event: str
    ) -> None:
        self.ensure_schema()
        with self.repo._conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM agent_observations
                WHERE correlation_id = %s AND agent_type = %s
                  AND session_id_hash = %s AND event_type = %s
                """,
                (scope, agent_type, session_hash, marker_event),
            )
        self.repo._conn.commit()

    def claim_context_marker(
        self, record: dict[str, Any], *, marker_event: str, max_observations: int, max_markers: int
    ) -> bool:
        return self.insert_unique_observation(
            record,
            scopes=(str(record["correlation_id"]),),
            marker_event=marker_event,
            max_observations=max_observations,
            max_markers=max_markers,
        )

    def recent_rows(
        self, *, scopes: tuple[str, ...], marker_event: str, limit: int
    ) -> list[dict[str, Any]]:
        self.ensure_schema()
        ph = self._placeholders(scopes)
        with self.repo._conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, agent_type, event_type, tool_name, summary,
                       metadata_json, created_at
                FROM agent_observations
                WHERE correlation_id IN ({ph}) AND event_type != %s
                ORDER BY created_at DESC, id DESC LIMIT %s
                """,
                (*scopes, marker_event, limit),
            )
            return [dict(row) for row in cur.fetchall()]

    def fts_rows(
        self, *, scopes: tuple[str, ...], query: str, marker_event: str, limit: int
    ) -> list[dict[str, Any]]:
        del query
        return []

    def scan_rows(
        self, *, scopes: tuple[str, ...], marker_event: str, limit: int
    ) -> list[dict[str, Any]]:
        return self.recent_rows(scopes=scopes, marker_event=marker_event, limit=limit)

    def observation_metadata(self, *, memory_id: str, scopes: tuple[str, ...]) -> str | None:
        self.ensure_schema()
        ph = self._placeholders(scopes)
        with self.repo._conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT metadata_json FROM agent_observations
                WHERE id = %s AND correlation_id IN ({ph})
                """,
                (memory_id, *scopes),
            )
            row = cur.fetchone()
        return None if row is None else str(row["metadata_json"])

    def update_observation_metadata(self, *, memory_id: str, metadata_json: str) -> bool:
        with self.repo._conn.cursor() as cur:
            cur.execute(
                "UPDATE agent_observations SET metadata_json = %s WHERE id = %s",
                (metadata_json, memory_id),
            )
            changed = cur.rowcount == 1
        self.repo._conn.commit()
        return changed

    def session_rows(
        self, *, scopes: tuple[str, ...], session_hash: str, limit: int
    ) -> list[dict[str, Any]]:
        self.ensure_schema()
        ph = self._placeholders(scopes)
        with self.repo._conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT created_at FROM agent_observations
                WHERE correlation_id IN ({ph}) AND session_id_hash = %s
                  AND event_type = 'session_capsule'
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (*scopes, session_hash),
            )
            capsule = cur.fetchone()
            after = capsule["created_at"] if capsule else datetime.min.replace(tzinfo=timezone.utc)
            cur.execute(
                f"""
                SELECT id, agent_type, event_type, tool_name, summary,
                       metadata_json, created_at
                FROM agent_observations
                WHERE correlation_id IN ({ph}) AND session_id_hash = %s
                  AND event_type IN (
                      'user_intent', 'tool_result', 'tool_failure', 'repository_change'
                  )
                  AND created_at > %s
                ORDER BY created_at ASC, id ASC LIMIT %s
                """,
                (*scopes, session_hash, after, limit),
            )
            return [dict(row) for row in cur.fetchall()]

    def forget(self, *, memory_id: str, scopes: tuple[str, ...]) -> bool:
        self.ensure_schema()
        ph = self._placeholders(scopes)
        with self.repo._conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM agent_observations WHERE id = %s AND correlation_id IN ({ph})",
                (memory_id, *scopes),
            )
            changed = cur.rowcount == 1
        self.repo._conn.commit()
        return changed

    def clear(self, *, scopes: tuple[str, ...], checkout_id: str) -> int:
        self.ensure_schema()
        ph = self._placeholders(scopes)
        with self.repo._conn.cursor() as cur:
            cur.execute(f"DELETE FROM agent_observations WHERE correlation_id IN ({ph})", scopes)
            count = int(cur.rowcount)
            cur.execute("DELETE FROM repository_snapshots WHERE workspace_id = %s", (checkout_id,))
        self.repo._conn.commit()
        return count

    def upsert_snapshot(
        self,
        *,
        checkout_id: str,
        digest: str,
        summary: str,
        updated_at: str,
        observation: dict[str, Any] | None,
        record_initial: bool,
        marker_event: str,
        max_observations: int,
        max_markers: int,
    ) -> bool:
        self.ensure_schema()
        with self.repo._conn.cursor() as cur:
            cur.execute(
                "SELECT digest FROM repository_snapshots WHERE workspace_id = %s", (checkout_id,)
            )
            previous = cur.fetchone()
            cur.execute(
                """
                INSERT INTO repository_snapshots (workspace_id, digest, summary, updated_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT(workspace_id) DO UPDATE SET
                    digest = EXCLUDED.digest,
                    summary = EXCLUDED.summary,
                    updated_at = EXCLUDED.updated_at
                """,
                (checkout_id, digest, summary, updated_at),
            )
            changed = previous is None or str(previous["digest"]) != digest
            should_record = changed and (previous is not None or record_initial)
            if observation is not None and should_record:
                self._insert(cur, observation)
                self._prune(
                    cur,
                    scope=str(observation["correlation_id"]),
                    marker_event=marker_event,
                    max_observations=max_observations,
                    max_markers=max_markers,
                )
        self.repo._conn.commit()
        return should_record

    def stats(self, *, scopes: tuple[str, ...]) -> dict[str, Any]:
        self.ensure_schema()
        ph = self._placeholders(scopes)
        with self.repo._conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT event_type, metadata_json, COUNT(*) AS count
                FROM agent_observations
                WHERE correlation_id IN ({ph})
                GROUP BY event_type, metadata_json
                """,
                scopes,
            )
            rows = cur.fetchall()
            cur.execute(
                f"SELECT COUNT(*) AS count FROM agent_observations WHERE correlation_id IN ({ph})",
                scopes,
            )
            total = cur.fetchone()
        return {"total": int(total["count"] if total else 0), "rows": [dict(row) for row in rows]}

    def compact(
        self, *, scopes: tuple[str, ...], keep_recent: int, dry_run: bool
    ) -> dict[str, int]:
        self.ensure_schema()
        ph = self._placeholders(scopes)
        keep = max(1, int(keep_recent))
        with self.repo._conn.cursor() as cur:
            cur.execute(
                f"""
                WITH ranked AS (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY correlation_id, event_type, summary
                               ORDER BY created_at DESC, id DESC
                           ) AS duplicate_rank
                    FROM agent_observations
                    WHERE correlation_id IN ({ph})
                      AND event_type NOT IN ('user_intent', 'context_injected')
                )
                SELECT id FROM ranked WHERE duplicate_rank > 1
                """,
                scopes,
            )
            duplicate_ids = {str(row["id"]) for row in cur.fetchall()}
            cur.execute(
                f"""
                WITH inactive AS (
                    SELECT id,
                           ROW_NUMBER() OVER (ORDER BY created_at DESC, id DESC) AS inactive_rank
                    FROM agent_observations
                    WHERE correlation_id IN ({ph})
                      AND metadata_json ~
                          '"memory_status":"(resolved|superseded|stale|contradicted)"'
                )
                SELECT id FROM inactive WHERE inactive_rank > %s
                """,
                (*scopes, keep),
            )
            inactive_ids = {str(row["id"]) for row in cur.fetchall()}
            ids = sorted(duplicate_ids | inactive_ids)
            if ids and not dry_run:
                delete_ph = self._placeholders(tuple(ids))
                cur.execute(
                    f"DELETE FROM agent_observations WHERE id IN ({delete_ph})", tuple(ids)
                )
        if ids and not dry_run:
            self.repo._conn.commit()
        return {
            "duplicates": len(duplicate_ids),
            "inactive": len(inactive_ids),
            "total": len(ids),
        }


def memory_repository(repo: Any) -> MemoryRepository:
    """Return the formal passive-memory adapter for a queue repository."""

    if hasattr(repo, "_connection"):
        return SQLiteMemoryRepository(repo)
    if hasattr(repo, "_conn"):
        return PostgresMemoryRepository(repo)
    raise TypeError(f"unsupported repository adapter: {type(repo).__name__}")
