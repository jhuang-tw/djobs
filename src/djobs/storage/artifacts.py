"""SQLite/PostgreSQL persistence for bounded canonical typed memory.

This adapter owns SQL/transactions only. Artifact authority, source eligibility,
and human review are decided by the application service, never by this store.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from typing import Any

from djobs.memory_artifacts import MAX_ARTIFACTS, MAX_SOURCES, ArtifactError
from djobs.storage.schema import MEMORY_ARTIFACT_SCHEMA_SQL

ARTIFACT_SCHEMA_VERSION = 1


def table_exists(cursor: Any, sqlite: bool, table: str) -> bool:
    if sqlite:
        row = cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        return row is not None
    cursor.execute("SELECT to_regclass(%s) AS name", (table,))
    row = cursor.fetchone()
    return bool(row and row["name"])


def _artifact_schema_available(cursor: Any, sqlite: bool) -> bool:
    if not table_exists(cursor, sqlite, "memory_artifact_sources"):
        return False
    result = cursor.execute("SELECT version FROM djobs_memory_schema WHERE component='artifacts'")
    row = result.fetchone()
    if row is None or int(row["version"]) != ARTIFACT_SCHEMA_VERSION:
        raise ArtifactError("unsupported_artifact_schema")
    return True


def protected_observations(cursor: Any, sqlite: bool, scopes: tuple[str, ...] = ()) -> set[str]:
    """Protect active provenance, scoped to the raw records being maintained."""
    if not _artifact_schema_available(cursor, sqlite):
        return set()
    placeholder = "?" if sqlite else "%s"
    restriction = ""
    params: tuple = ()
    if scopes:
        marks = ",".join(placeholder for _ in scopes)
        restriction = (
            f" AND (repo_family_id IN ({marks}) OR id IN ("
            "SELECT s.artifact_id FROM memory_artifact_sources s "
            "JOIN agent_observations o ON o.id=s.observation_id "
            f"WHERE o.correlation_id IN ({marks})))"
        )
        params = (*scopes, *scopes)
    result = cursor.execute(
        "WITH RECURSIVE kept(id) AS ("
        " SELECT id FROM memory_artifacts WHERE status='active' "
        " AND authority IN ('human_accepted','deterministic_derived')"
        + restriction
        + " UNION SELECT s.source_artifact_id FROM memory_artifact_sources s "
        " JOIN kept k ON k.id=s.artifact_id WHERE s.source_artifact_id IS NOT NULL"
        ") SELECT DISTINCT s.observation_id FROM memory_artifact_sources s "
        "JOIN kept k ON k.id=s.artifact_id WHERE s.observation_id IS NOT NULL",
        params,
    )
    return {str(row["observation_id"]) for row in result.fetchall()}


def prune_observations(
    cursor: Any,
    sqlite: bool,
    *,
    scope: str,
    marker_event: str,
    max_observations: int,
    max_markers: int,
) -> None:
    """Retention stays bounded while pinning active reviewed provenance."""
    placeholder = "?" if sqlite else "%s"
    protected = protected_observations(cursor, sqlite, (scope,))
    if not protected:
        cursor.execute(
            "DELETE FROM agent_observations WHERE correlation_id="
            + placeholder
            + " AND event_type != "
            + placeholder
            + " AND id NOT IN ("
            "SELECT id FROM agent_observations WHERE correlation_id="
            + placeholder
            + " AND event_type != "
            + placeholder
            + " ORDER BY created_at DESC,id DESC LIMIT "
            + placeholder
            + ")",
            (scope, marker_event, scope, marker_event, max_observations),
        )
    else:
        rows = cursor.execute(
            "SELECT id FROM agent_observations WHERE correlation_id="
            + placeholder
            + " AND event_type != "
            + placeholder
            + " ORDER BY created_at DESC,id DESC",
            (scope, marker_event),
        ).fetchall()
        ids = [str(row["id"]) for row in rows]
        protected.intersection_update(ids)
        if len(protected) > max_observations:
            raise ArtifactError("provenance_retention_capacity")
        keep = set(protected)
        keep.update(
            [item for item in ids if item not in protected][: max_observations - len(protected)]
        )
        remove = [item for item in ids if item not in keep]
        forget_source_dependents(cursor, sqlite, remove)
        for offset in range(0, len(remove), 256):
            batch = remove[offset : offset + 256]
            cursor.execute(
                "DELETE FROM agent_observations WHERE id IN ("
                + ",".join(placeholder for _ in batch)
                + ")",
                tuple(batch),
            )
    cursor.execute(
        "DELETE FROM agent_observations WHERE correlation_id="
        + placeholder
        + " AND event_type = "
        + placeholder
        + " AND id NOT IN ("
        "SELECT id FROM agent_observations WHERE correlation_id="
        + placeholder
        + " AND event_type = "
        + placeholder
        + " ORDER BY created_at DESC,id DESC LIMIT "
        + placeholder
        + ")",
        (scope, marker_event, scope, marker_event, max_markers),
    )


def forget_source_dependents(cursor: Any, sqlite: bool, observation_ids: list[str]) -> int:
    """Delete derived content, including transitive children, before its source."""
    if not observation_ids or not _artifact_schema_available(cursor, sqlite):
        return 0
    placeholder = "?" if sqlite else "%s"
    placeholders = ",".join(placeholder for _ in observation_ids)
    result = cursor.execute(
        "WITH RECURSIVE affected(id) AS ("
        " SELECT artifact_id FROM memory_artifact_sources "
        f"WHERE observation_id IN ({placeholders})"
        " UNION SELECT s.artifact_id FROM memory_artifact_sources s "
        " JOIN affected a ON s.source_artifact_id=a.id"
        ") SELECT id FROM affected",
        tuple(observation_ids),
    )
    ids = [str(row["id"]) for row in result.fetchall()]
    if ids:
        cursor.execute(
            "DELETE FROM memory_artifacts WHERE id IN ("
            + ",".join(placeholder for _ in ids)
            + ")",
            tuple(ids),
        )
    return len(ids)


class ArtifactStore:
    def __init__(self, repo: Any) -> None:
        self.repo = repo
        self.sqlite = hasattr(repo, "_connection")
        if not self.sqlite and not hasattr(repo, "_conn"):
            raise ArtifactError("unsupported_repository")

    def sql(self, value: str) -> str:
        return value if self.sqlite else value.replace("?", "%s")

    def execute(self, cursor: Any, query: str, parameters: tuple = ()) -> Any:
        cursor.execute(self.sql(query), parameters)
        return cursor

    @contextmanager
    def transaction(self, *, write: bool = False, family: str = ""):
        if self.sqlite:
            with self.repo._lock, self.repo.transaction(immediate=write):
                cursor = self.repo._connection.cursor()
                try:
                    yield cursor
                finally:
                    cursor.close()
        else:
            from psycopg.pq import TransactionStatus

            owns_transaction = self.repo._conn.info.transaction_status == TransactionStatus.IDLE
            with self.repo._conn.transaction(), self.repo._conn.cursor() as cursor:
                if not write:
                    if owns_transaction:
                        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                    else:
                        cursor.execute("SHOW transaction_isolation")
                        isolation = cursor.fetchone()["transaction_isolation"]
                        if isolation not in {"repeatable read", "serializable"}:
                            raise ArtifactError("outer_transaction_requires_snapshot_isolation")
                if write:
                    cursor.execute("SET LOCAL lock_timeout = '100ms'")
                    cursor.execute("SET LOCAL statement_timeout = '1500ms'")
                    lock_id = int.from_bytes(
                        hashlib.sha256(family.encode()).digest()[:8], "big", signed=True
                    )
                    cursor.execute("SELECT pg_advisory_xact_lock(%s)", (lock_id,))
                yield cursor

    def version(self, cursor: Any) -> int | None:
        if not table_exists(cursor, self.sqlite, "djobs_memory_schema"):
            return None
        row = self.execute(
            cursor, "SELECT version FROM djobs_memory_schema WHERE component=?", ("artifacts",)
        ).fetchone()
        if row is None:
            return None
        version = int(row["version"])
        if version != ARTIFACT_SCHEMA_VERSION:
            raise ArtifactError("unsupported_artifact_schema")
        return version

    def ensure_schema(self, cursor: Any) -> None:
        if self.version(cursor) == ARTIFACT_SCHEMA_VERSION:
            return
        for statement in MEMORY_ARTIFACT_SCHEMA_SQL.split(";"):
            if statement.strip():
                cursor.execute(statement)
        self.execute(
            cursor,
            "INSERT INTO djobs_memory_schema(component,version) VALUES (?,?) "
            "ON CONFLICT(component) DO NOTHING",
            ("artifacts", ARTIFACT_SCHEMA_VERSION),
        )

    def snapshot(self, cursor: Any, family: str, *, lock: bool = False) -> dict[str, Any]:
        if self.version(cursor) is None:
            return {
                "artifacts": {},
                "sources": {},
                "observations": {},
                "relations": [],
                "reviews": [],
            }
        locking = " FOR UPDATE" if lock and not self.sqlite else ""
        records = self.execute(
            cursor,
            "SELECT * FROM memory_artifacts WHERE repo_family_id=? ORDER BY id LIMIT ?" + locking,
            (family, MAX_ARTIFACTS + 1),
        ).fetchall()
        if len(records) > MAX_ARTIFACTS:
            raise ArtifactError("artifact_capacity_exceeded")
        artifacts = {str(row["id"]): dict(row) for row in records}
        source_rows = self.execute(
            cursor,
            "SELECT s.* FROM memory_artifact_sources s "
            "JOIN memory_artifacts a ON a.id=s.artifact_id "
            "WHERE a.repo_family_id=? ORDER BY s.artifact_id,s.source_kind,s.source_id LIMIT ?",
            (family, MAX_ARTIFACTS * MAX_SOURCES + 1),
        ).fetchall()
        if len(source_rows) > MAX_ARTIFACTS * MAX_SOURCES:
            raise ArtifactError("source_capacity_exceeded")
        sources: dict[str, list[dict[str, Any]]] = {key: [] for key in artifacts}
        for row in source_rows:
            sources[str(row["artifact_id"])].append(dict(row))
        observation_ids = sorted(
            {
                str(row["observation_id"])
                for row in source_rows
                if row["observation_id"] is not None
            }
        )
        observations = self.observations(cursor, observation_ids, lock=lock)
        relations = [
            dict(row)
            for row in self.execute(
                cursor,
                "SELECT r.* FROM memory_relations r JOIN memory_artifacts a ON a.id=r.source_id "
                "WHERE a.repo_family_id=? ORDER BY r.source_id,r.target_id,r.kind LIMIT ?",
                (family, MAX_ARTIFACTS * MAX_SOURCES + 1),
            ).fetchall()
        ]
        reviews = [
            dict(row)
            for row in self.execute(
                cursor,
                "SELECT r.* FROM memory_reviews r JOIN memory_artifacts a ON a.id=r.artifact_id "
                "WHERE a.repo_family_id=? ORDER BY r.created_at,r.id LIMIT ?",
                (family, MAX_ARTIFACTS * 8 + 1),
            ).fetchall()
        ]
        if len(relations) > MAX_ARTIFACTS * MAX_SOURCES or len(reviews) > MAX_ARTIFACTS * 8:
            raise ArtifactError("artifact_history_bound")
        return {
            "artifacts": artifacts,
            "sources": sources,
            "observations": observations,
            "relations": relations,
            "reviews": reviews,
        }

    def observations(self, cursor: Any, ids: list[str], *, lock: bool = False) -> dict[str, Any]:
        result = {}
        for offset in range(0, len(ids), 256):
            batch = ids[offset : offset + 256]
            locking = " FOR SHARE" if lock and not self.sqlite else ""
            query = (
                "SELECT * FROM agent_observations WHERE id IN ("
                + ",".join("?" for _ in batch)
                + ")"
                + locking
            )
            for row in self.execute(cursor, query, tuple(batch)).fetchall():
                result[str(row["id"])] = dict(row)
        return result

    def insert(self, cursor: Any, row: dict[str, Any], sources: list[dict[str, Any]]) -> None:
        columns = tuple(row)
        self.execute(
            cursor,
            "INSERT INTO memory_artifacts ("
            + ",".join(columns)
            + ") VALUES ("
            + ",".join("?" for _ in columns)
            + ")",
            tuple(row[key] for key in columns),
        )
        for source in sources:
            columns = tuple(source)
            self.execute(
                cursor,
                "INSERT INTO memory_artifact_sources ("
                + ",".join(columns)
                + ") VALUES ("
                + ",".join("?" for _ in columns)
                + ")",
                tuple(source[key] for key in columns),
            )

    def review_receipt(self, cursor: Any, row: dict[str, Any]) -> None:
        columns = tuple(row)
        self.execute(
            cursor,
            "INSERT INTO memory_reviews ("
            + ",".join(columns)
            + ") VALUES ("
            + ",".join("?" for _ in columns)
            + ")",
            tuple(row[key] for key in columns),
        )

    def clear(self, family: str) -> int:
        with self.transaction(write=True, family=family) as cursor:
            if self.version(cursor) is None:
                return 0
            self.execute(cursor, "DELETE FROM memory_artifacts WHERE repo_family_id=?", (family,))
            return int(cursor.rowcount)
