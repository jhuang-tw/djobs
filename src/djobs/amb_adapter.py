"""Clean-room retrieval-only bridge for the inspected AMB provider contract.

No AMB dependency is imported until create_amb_provider() is explicitly called.
All ingestion is confined to an explicitly prepared, owner-marked benchmark DB,
never the user's djobs store. No model, generator, judge or task is created.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
import sqlite3
import stat
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from djobs.memory_artifacts import ArtifactError, canonical_json
from djobs.privacy import REDACTION_VERSION, redact_text
from djobs.retrieval import retrieve_memory
from djobs.storage.memory import memory_repository
from djobs.storage.read_only import connect_read_only
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.workspace import Workspace

_OWNER = "djobs-amb-retrieval-v1"
_FILENAME = "djobs-benchmark.db"
_MAX_UNITS = 256
_MAX_DOCUMENTS = 1000


def _safe_id(value: Any) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 200 or redact_text(value) != value:
        raise ArtifactError("invalid_benchmark_identity")
    if any(ord(character) < 32 for character in value):
        raise ArtifactError("invalid_benchmark_identity")
    return value


def _unit(value: str | None) -> str:
    return "default" if value is None else "user:" + _safe_id(value)


class DjobsBenchmarkProvider:
    name = "djobs-native-lexical"
    description = "Bounded native lexical retrieval; no answer model or judge"
    kind = "local"
    provider = "djobs"
    variant = "native-lexical"
    concurrency = 1
    supports_filters = False

    def __init__(self, *, document_factory: Callable[..., Any]) -> None:
        self.document_factory = document_factory
        self.repo: SQLiteJobRepository | None = None
        self.root: Path | None = None
        self.units: set[str] | None = None
        self.last_ingestion: dict[str, Any] = {}

    def initialize(self) -> None:
        """Initialization alone performs no filesystem or network operation."""

    def cleanup(self) -> None:
        if self.repo is not None:
            self.repo.close()
            self.repo = None

    @staticmethod
    def _verify_owner(path: Path) -> None:
        if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
            raise ArtifactError("benchmark_database_link_refused")
        connection = connect_read_only(path)
        if connection is None:
            raise ArtifactError("benchmark_database_owner_missing")
        try:
            row = connection.execute("SELECT owner,version FROM djobs_benchmark_owner").fetchall()
            if len(row) != 1 or row[0]["owner"] != _OWNER or row[0]["version"] != 1:
                raise ArtifactError("benchmark_database_owner_mismatch")
        except sqlite3.DatabaseError:
            raise ArtifactError("benchmark_database_owner_mismatch") from None
        finally:
            connection.close()

    def prepare(
        self, store_dir: Path, unit_ids: set[str] | None = None, reset: bool = True
    ) -> None:
        self.cleanup()
        directory = Path(store_dir).expanduser().absolute()
        if directory.exists():
            info = directory.lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400
            ):
                raise ArtifactError("real_benchmark_directory_required")
        else:
            directory.mkdir(parents=True)
        units = None if unit_ids is None else {_unit(value) for value in unit_ids}
        if units is not None and len(units) > _MAX_UNITS:
            raise ArtifactError("benchmark_unit_bound")
        path = directory / _FILENAME
        if path.exists() or path.is_symlink():
            self._verify_owner(path)
        else:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    "CREATE TABLE djobs_benchmark_owner("
                    "owner TEXT NOT NULL,version INTEGER NOT NULL)"
                )
                connection.execute("INSERT INTO djobs_benchmark_owner VALUES (?,?)", (_OWNER, 1))
                connection.commit()
            finally:
                connection.close()
        repo = SQLiteJobRepository.from_path(path)
        try:
            memory_repository(repo).ensure_schema()
            if reset:
                # The dedicated ownership marker was verified before opening for writes.
                with repo.transaction(immediate=True) as transaction:
                    transaction.execute("DELETE FROM agent_observations")
            self.repo, self.root, self.units = repo, directory.resolve(), units
        except Exception:
            repo.close()
            raise

    def _workspace(self, unit: str) -> Workspace:
        if self.repo is None or self.root is None:
            raise ArtifactError("benchmark_prepare_required")
        if self.units is not None and unit not in self.units:
            raise ArtifactError("benchmark_unit_not_declared")
        identity = hashlib.sha256(unit.encode()).hexdigest()
        family, checkout = "family:amb:" + identity, "repo:amb:" + identity
        return Workspace(
            root=str(self.root),
            workspace_id=checkout,
            checkout_id=checkout,
            repo_family_id=family,
            correlation_ids=(checkout,),
            memory_correlation_ids=(family,),
            source="benchmark",
        )

    def ingest(self, documents: list[Any]) -> None:
        if self.repo is None:
            raise ArtifactError("benchmark_prepare_required")
        if not isinstance(documents, list) or len(documents) > _MAX_DOCUMENTS:
            raise ArtifactError("benchmark_batch_bound")
        staged: dict[str, dict[str, Any]] = {}
        truncated = 0
        for document in documents:
            unit = _unit(getattr(document, "user_id", None))
            workspace = self._workspace(unit)
            identity = _safe_id(document.id)
            if not isinstance(document.content, str) or len(document.content) > 1_000_000:
                raise ArtifactError("benchmark_content_bound")
            redacted = redact_text(document.content)
            truncated += len(redacted) > 2000
            record_id = "amb_" + hashlib.sha256((unit + "\0" + identity).encode()).hexdigest()
            metadata = {
                "memory_status": "active",
                "authority": "raw_observation",
                "stored_as_data": True,
                "repo_family_id": workspace.repo_family_id,
                "original_document_id": identity,
                "benchmark_unit": unit,
                "redaction_version": REDACTION_VERSION,
                "content_truncated": len(redacted) > 2000,
                "input_redacted_hash": hashlib.sha256(redacted.encode()).hexdigest(),
            }
            row = {
                "id": record_id,
                "correlation_id": workspace.repo_family_id,
                "agent_type": "amb-fixture",
                "session_id_hash": None,
                "event_type": "tool_result",
                "tool_name": "benchmark-ingestion",
                "summary": redacted[:2000],
                "metadata_json": canonical_json(metadata),
                "created_at": "2020-01-01T00:00:00+00:00",
            }
            if record_id in staged and staged[record_id] != row:
                raise ArtifactError("benchmark_document_identity_conflict")
            staged[record_id] = row
        inserted = 0
        adapter = memory_repository(self.repo)
        with self.repo.transaction(immediate=True) as transaction:
            pending = []
            for row in staged.values():
                existing = transaction.execute(
                    "SELECT summary,metadata_json FROM agent_observations WHERE id=?", (row["id"],)
                ).fetchone()
                if existing:
                    if (
                        existing["summary"] != row["summary"]
                        or existing["metadata_json"] != row["metadata_json"]
                    ):
                        raise ArtifactError("benchmark_document_identity_conflict")
                else:
                    pending.append(row)
            counts = {
                row["correlation_id"]: row["n"]
                for row in transaction.execute(
                    "SELECT correlation_id,COUNT(*) AS n FROM agent_observations "
                    "GROUP BY correlation_id"
                )
            }
            additions = Counter(row["correlation_id"] for row in pending)
            if len(set(counts) | set(additions)) > _MAX_UNITS or any(
                counts.get(scope, 0) + amount > _MAX_DOCUMENTS
                for scope, amount in additions.items()
            ):
                raise ArtifactError("benchmark_retention_bound")
            for row in pending:
                adapter.insert_observation(
                    row,
                    marker_event="context_injected",
                    max_observations=_MAX_DOCUMENTS,
                    max_markers=16,
                )
                inserted += 1
        self.last_ingestion = {
            "inserted": inserted,
            "duplicates": len(documents) - inserted,
            "truncated_documents": truncated,
            "model_calls": 0,
            "external_network_calls": 0,
        }

    def retrieve(
        self,
        query: str,
        k: int = 10,
        user_id: str | None = None,
        query_timestamp: str | None = None,
        filters: dict | None = None,
    ) -> tuple[list, dict]:
        if filters is not None or query_timestamp is not None:
            raise ArtifactError("benchmark_filter_or_historical_query_not_supported")
        if self.repo is None:
            raise ArtifactError("benchmark_prepare_required")
        workspace = self._workspace(_unit(user_id))
        result = retrieve_memory(self.repo, workspace, query, limit=k)
        documents = []
        for item in result.items:
            row = self.repo.read_one(
                "SELECT metadata_json FROM agent_observations WHERE id=?", (item["id"],)
            )
            if row is None:
                continue
            metadata = json.loads(row["metadata_json"])
            original = metadata["original_document_id"]
            documents.append(
                self.document_factory(
                    id=original, content=item["summary"], user_id=user_id, source_ids=[original]
                )
            )
        return documents, {
            "retrieval": result.trace,
            "provider": self.name,
            "bounded_k": min(max(1, int(k)), 20),
            "max_documents_per_unit": _MAX_DOCUMENTS,
            "generation": "not_run",
            "answer_judge": "not_run",
            "model_calls": 0,
            "external_network_calls": 0,
        }

    async def async_ingest(self, documents: list[Any]) -> None:
        await asyncio.to_thread(self.ingest, documents)

    async def async_retrieve(
        self,
        query: str,
        k: int = 10,
        user_id: str | None = None,
        query_timestamp: str | None = None,
        filters: dict | None = None,
    ):
        return await asyncio.to_thread(self.retrieve, query, k, user_id, query_timestamp, filters)

    def direct_answer(self, *args, **kwargs):
        raise NotImplementedError("djobs benchmark bridge does not generate or judge answers")


def create_amb_provider():
    """Load the caller-installed AMB interface only for an explicitly requested run."""
    base = importlib.import_module("memory_bench.memory.base").MemoryProvider
    document = importlib.import_module("memory_bench.models").Document

    class NativeAmbProvider(DjobsBenchmarkProvider, base):
        def __init__(self):
            DjobsBenchmarkProvider.__init__(self, document_factory=document)

    return NativeAmbProvider()
