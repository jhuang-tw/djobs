"""Native bounded retrieval index storage; observations remain the only authority.

Schema creation and replacement are explicit maintenance writes. Every read is
SELECT-only, including missing/future-schema checks. SQLite and PostgreSQL share
this bounded contract and vector representation; no vector database is needed.
"""

from __future__ import annotations

import hashlib
import json
import struct
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from typing import Any

from djobs.embedding import EmbeddingIdentity, normalize_vector
from djobs.memory_policy import CANDIDATE_BOUND, source_hash
from djobs.storage.schema import MEMORY_RETRIEVAL_SCHEMA_SQL

INDEX_SCHEMA_VERSION = 1


def snapshot_digest(rows: list[dict[str, Any]]) -> str:
    values = sorted((str(row["id"]), source_hash(row)) for row in rows)
    return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


def pack_vector(values: tuple[float, ...], dimension: int) -> bytes:
    normalized = normalize_vector(values, dimension)
    return struct.pack(f"<{dimension}f", *normalized)


def unpack_vector(value: Any, dimension: int) -> tuple[float, ...]:
    blob = bytes(value)
    if len(blob) != dimension * 4:
        raise ValueError("invalid vector byte length")
    return normalize_vector(struct.unpack(f"<{dimension}f", blob), dimension)


class RetrievalIndex:
    def __init__(self, repo: Any) -> None:
        self.repo = repo
        self.sqlite = hasattr(repo, "_connection")
        if not self.sqlite and not hasattr(repo, "_conn"):
            raise ValueError("unsupported memory repository")

    def _sql(self, value: str) -> str:
        return value if self.sqlite else value.replace("?", "%s")

    @contextmanager
    def _read_cursor(self):
        if self.sqlite:
            with self.repo._lock:
                cursor = self.repo._connection.cursor()
                try:
                    yield cursor
                finally:
                    cursor.close()
        else:
            with self.repo._conn.transaction(), self.repo._conn.cursor() as cursor:
                yield cursor

    @contextmanager
    def _write_cursor(self):
        if self.sqlite:
            with self.repo.transaction(immediate=True):
                cursor = self.repo._connection.cursor()
                try:
                    yield cursor
                finally:
                    cursor.close()
        else:
            with self.repo._conn.transaction(), self.repo._conn.cursor() as cursor:
                yield cursor

    def _table_exists(self, cursor: Any, name: str) -> bool:
        if self.sqlite:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
            return cursor.fetchone() is not None
        cursor.execute("SELECT to_regclass(%s) AS name", (name,))
        row = cursor.fetchone()
        return bool(row and row["name"])

    def _version(self, cursor: Any) -> int | None:
        if not self._table_exists(cursor, "djobs_memory_schema"):
            return None
        cursor.execute(
            self._sql("SELECT version FROM djobs_memory_schema WHERE component=?"), ("retrieval",)
        )
        row = cursor.fetchone()
        return None if row is None else int(row["version"])

    def status(self) -> str:
        with self._read_cursor() as cursor:
            version = self._version(cursor)
        if version is None:
            return "missing"
        return "available" if version == INDEX_SCHEMA_VERSION else "unsupported_schema"

    @staticmethod
    def _source_sql(scopes: tuple[str, ...]) -> str:
        placeholders = ",".join("?" for _ in scopes)
        return (
            "SELECT id, correlation_id, session_id_hash, agent_type, event_type, "
            "tool_name, summary, metadata_json, created_at FROM agent_observations "
            f"WHERE correlation_id IN ({placeholders}) AND event_type != ? "
            "ORDER BY created_at DESC, id DESC LIMIT ?"
        )

    def source_rows(self, scopes: tuple[str, ...]) -> list[dict[str, Any]]:
        if not scopes:
            return []
        with self._read_cursor() as cursor:
            cursor.execute(
                self._sql(self._source_sql(scopes)), (*scopes, "context_injected", CANDIDATE_BOUND)
            )
            return [dict(row) for row in cursor.fetchall()]

    def load(
        self, family: str, identity: EmbeddingIdentity, sources: list[dict[str, Any]]
    ) -> tuple[str, dict[str, tuple[float, ...]], dict[str, Any]]:
        with self._read_cursor() as cursor:
            version = self._version(cursor)
            if version is None:
                return "missing", {}, {}
            if version != INDEX_SCHEMA_VERSION:
                return "unsupported_schema", {}, {}
            cursor.execute(
                self._sql("SELECT * FROM memory_embedding_indexes WHERE repo_family_id=?"),
                (family,),
            )
            found = cursor.fetchone()
            if found is None:
                return "missing", {}, {}
            metadata = dict(found)
            if metadata["identity_hash"] != identity.fingerprint:
                return "identity_mismatch", {}, {}
            if metadata["source_digest"] != snapshot_digest(sources):
                return "stale", {}, {}
            cursor.execute(
                self._sql(
                    "SELECT record_id, content_hash, vector_bytes FROM memory_embeddings "
                    "WHERE repo_family_id=? AND identity_hash=? ORDER BY record_id LIMIT 1001"
                ),
                (family, identity.fingerprint),
            )
            rows = [dict(row) for row in cursor.fetchall()]
        if len(rows) != metadata["record_count"] or len(rows) > CANDIDATE_BOUND:
            return "incomplete", {}, {}
        from djobs.memory_policy import content_hash

        source_map = {str(row["id"]): row for row in sources}
        vectors = {}
        try:
            for row in rows:
                record_id = str(row["record_id"])
                source = source_map.get(record_id)
                if source is None or content_hash(source) != row["content_hash"]:
                    return "stale", {}, {}
                vectors[record_id] = unpack_vector(row["vector_bytes"], identity.dimension)
        except (TypeError, ValueError, struct.error):
            return "corrupt", {}, {}
        return (
            "ready",
            vectors,
            {
                "record_count": len(rows),
                "vector_bytes": len(rows) * identity.dimension * 4,
                "identity_hash": identity.fingerprint,
            },
        )

    def replace(
        self,
        *,
        family: str,
        scopes: tuple[str, ...],
        identity: EmbeddingIdentity,
        sources: list[dict[str, Any]],
        records: list[tuple[str, str, bytes, tuple[str, ...]]],
    ) -> None:
        if len(records) > CANDIDATE_BOUND or len({item[0] for item in records}) != len(records):
            raise ValueError("invalid bounded index replacement")
        if not scopes or family not in scopes:
            raise ValueError("index family is not in the authorized scopes")
        source_map = {str(row["id"]): row for row in sources}
        if any(item[0] not in source_map for item in records):
            raise ValueError("index record is outside the source snapshot")
        from djobs.memory_policy import content_hash

        if any(item[1] != content_hash(source_map[item[0]]) for item in records):
            raise ValueError("index content hash differs from canonical source")
        # Validate representations before any transaction/schema mutation.
        for _record_id, _content_hash, blob, _entities in records:
            unpack_vector(blob, identity.dimension)
        lock = self.repo._lock if self.sqlite else nullcontext()
        with lock:
            if self.status() == "unsupported_schema":
                raise ValueError("unsupported future retrieval schema")
            with self._write_cursor() as cursor:
                version = self._version(cursor)
                if version not in (None, INDEX_SCHEMA_VERSION):
                    raise ValueError("unsupported future retrieval schema")
                cursor.execute(
                    self._sql(self._source_sql(scopes)),
                    (*scopes, "context_injected", CANDIDATE_BOUND),
                )
                current = [dict(row) for row in cursor.fetchall()]
                if snapshot_digest(current) != snapshot_digest(sources):
                    raise ValueError("sources_changed_during_reindex")
                for statement in MEMORY_RETRIEVAL_SCHEMA_SQL.split(";"):
                    if statement.strip():
                        cursor.execute(
                            statement.replace(" BLOB ", " BYTEA ")
                            if not self.sqlite
                            else statement
                        )
                cursor.execute(
                    self._sql(
                        "INSERT INTO djobs_memory_schema(component,version) VALUES (?,?) "
                        "ON CONFLICT(component) DO NOTHING"
                    ),
                    ("retrieval", INDEX_SCHEMA_VERSION),
                )
                for table in (
                    "memory_entity_links",
                    "memory_embeddings",
                    "memory_embedding_indexes",
                ):
                    cursor.execute(
                        self._sql(f"DELETE FROM {table} WHERE repo_family_id=?"), (family,)
                    )
                created = datetime.now(timezone.utc).isoformat()
                for record_id, digest, blob, entities in records:
                    cursor.execute(
                        self._sql(
                            "INSERT INTO memory_embeddings "
                            "(record_id,repo_family_id,identity_hash,"
                            "content_hash,vector_bytes,created_at) "
                            "VALUES (?,?,?,?,?,?)"
                        ),
                        (record_id, family, identity.fingerprint, digest, blob, created),
                    )
                    for entity in entities[:128]:
                        kind, _, value = entity.partition(":")
                        cursor.execute(
                            self._sql(
                                "INSERT INTO memory_entity_links "
                                "(record_id,repo_family_id,kind,value,content_hash) "
                                "VALUES (?,?,?,?,?) "
                                "ON CONFLICT DO NOTHING"
                            ),
                            (record_id, family, kind, value, digest),
                        )
                cursor.execute(
                    self._sql(
                        "INSERT INTO memory_embedding_indexes "
                        "(repo_family_id,identity_hash,identity_json,"
                        "source_digest,record_count,created_at) "
                        "VALUES (?,?,?,?,?,?)"
                    ),
                    (
                        family,
                        identity.fingerprint,
                        identity.to_json(),
                        snapshot_digest(sources),
                        len(records),
                        created,
                    ),
                )

    def clear(self, family: str) -> None:
        if self.status() == "missing":
            return
        if self.status() != "available":
            raise ValueError("unsupported future retrieval schema")
        with self._write_cursor() as cursor:
            for table in ("memory_entity_links", "memory_embeddings", "memory_embedding_indexes"):
                cursor.execute(self._sql(f"DELETE FROM {table} WHERE repo_family_id=?"), (family,))
