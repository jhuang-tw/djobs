from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from djobs.amb_adapter import DjobsBenchmarkProvider, create_amb_provider
from djobs.memory_artifacts import ArtifactError


@dataclass
class FixtureDocument:
    id: str
    content: str
    user_id: str | None = None
    source_ids: list[str] | None = None


@pytest.fixture
def provider(tmp_path):
    value = DjobsBenchmarkProvider(document_factory=FixtureDocument)
    value.prepare(tmp_path / "benchmark")
    yield value
    value.cleanup()


def test_initialize_and_unprepared_use_do_not_create_global_state(tmp_path, monkeypatch):
    monkeypatch.setenv("DJOBS_DB", str(tmp_path / "must-not-create.db"))
    value = DjobsBenchmarkProvider(document_factory=FixtureDocument)
    value.initialize()
    with pytest.raises(ArtifactError, match="prepare_required"):
        value.retrieve("anything")
    assert not list(tmp_path.iterdir())


def test_real_native_retrieval_and_user_isolation(provider):
    provider.ingest(
        [
            FixtureDocument("one", "Parser keeps plus signs", "a"),
            FixtureDocument("two", "Separate user's private parser rule", "b"),
            FixtureDocument("three", "Unrelated footer formatting", "a"),
        ]
    )
    documents, trace = provider.retrieve("Parser", user_id="a")
    assert [document.id for document in documents] == ["one"]
    assert documents[0].source_ids == ["one"]
    assert trace["model_calls"] == trace["external_network_calls"] == 0
    assert trace["generation"] == "not_run"
    assert provider.retrieve("Parser")[0] == []
    assert [document.id for document in provider.retrieve("Parser", user_id="b")[0]] == ["two"]


def test_ingestion_redacts_discloses_truncation_and_deduplicates(provider):
    doc = FixtureDocument("one", "Parser API_KEY=synthetic-benchmark-secret " + "x" * 2300)
    provider.ingest([doc])
    assert provider.last_ingestion["truncated_documents"] == 1
    assert "synthetic-benchmark-secret" not in provider.retrieve("Parser")[0][0].content
    before = provider.repo._connection.total_changes
    provider.ingest([doc])
    assert provider.last_ingestion["duplicates"] == 1
    assert provider.repo._connection.total_changes == before
    with pytest.raises(ArtifactError, match="identity_conflict"):
        provider.ingest([FixtureDocument("one", "Changed immutable content")])


def test_ingestion_conflict_rolls_back_the_whole_new_batch(provider):
    provider.ingest([FixtureDocument("exists", "Original parser evidence")])
    with pytest.raises(ArtifactError, match="identity_conflict"):
        provider.ingest(
            [FixtureDocument("new", "New parser claim"), FixtureDocument("exists", "Altered")]
        )
    assert [doc.id for doc in provider.retrieve("parser")[0]] == ["exists"]


def test_unknown_database_is_never_migrated_reset_or_overwritten(tmp_path):
    directory = tmp_path / "benchmark"
    directory.mkdir()
    path = directory / "djobs-benchmark.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE real_user_content(value TEXT)")
        connection.execute("INSERT INTO real_user_content VALUES ('preserve')")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    value = DjobsBenchmarkProvider(document_factory=FixtureDocument)
    with pytest.raises(ArtifactError, match="owner_mismatch"):
        value.prepare(directory, reset=True)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute("SELECT value FROM real_user_content").fetchone()[0] == "preserve"
        )


def test_reopen_own_dataset_and_explicit_reset(provider):
    directory = provider.root
    provider.ingest([FixtureDocument("one", "Parser evidence")])
    provider.prepare(directory, reset=False)
    assert provider.retrieve("Parser")[0]
    provider.prepare(directory, reset=True)
    assert not provider.retrieve("Parser")[0]
    assert provider.repo._connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_declared_units_and_retention_bounds_are_not_silently_relaxed(tmp_path, monkeypatch):
    import djobs.amb_adapter as module

    value = DjobsBenchmarkProvider(document_factory=FixtureDocument)
    value.prepare(tmp_path / "limited", unit_ids={"allowed"})
    monkeypatch.setattr(module, "_MAX_DOCUMENTS", 2)
    try:
        with pytest.raises(ArtifactError, match="unit_not_declared"):
            value.ingest([FixtureDocument("one", "Parser", "other")])
        value.ingest(
            [
                FixtureDocument("one", "Parser first", "allowed"),
                FixtureDocument("two", "Parser second", "allowed"),
            ]
        )
        with pytest.raises(ArtifactError, match="retention_bound"):
            value.ingest([FixtureDocument("three", "Parser third", "allowed")])
        assert len(value.retrieve("Parser", user_id="allowed")[0]) == 2
    finally:
        value.cleanup()


def test_unsupported_temporal_filter_and_answer_modes_are_explicit(provider):
    with pytest.raises(ArtifactError, match="not_supported"):
        provider.retrieve("Parser", query_timestamp="2020-01-01T00:00:00Z")
    with pytest.raises(ArtifactError, match="not_supported"):
        provider.retrieve("Parser", filters={"tags": ["a"]})
    with pytest.raises(NotImplementedError, match="does not generate"):
        provider.direct_answer("Parser")


def test_async_interface_calls_native_retrieval(provider):
    asyncio.run(provider.async_ingest([FixtureDocument("one", "Parser asynchronous evidence")]))
    docs, trace = asyncio.run(provider.async_retrieve("Parser"))
    assert docs[0].id == "one" and trace["provider"] == provider.name


def test_explicit_factory_matches_inspected_amb_abstract_interface(tmp_path, monkeypatch):
    import djobs.amb_adapter as module

    class FixtureBase(ABC):
        @abstractmethod
        def ingest(self, documents): ...

        @abstractmethod
        def retrieve(self, query, k=10, user_id=None, query_timestamp=None, filters=None): ...

    imports = []

    def load(name):
        imports.append(name)
        return (
            SimpleNamespace(MemoryProvider=FixtureBase)
            if name.endswith("base")
            else SimpleNamespace(Document=FixtureDocument)
        )

    monkeypatch.setattr(module.importlib, "import_module", load)
    value = create_amb_provider()
    assert isinstance(value, FixtureBase)
    assert imports == ["memory_bench.memory.base", "memory_bench.models"]
    try:
        value.prepare(tmp_path / "factory")
        value.ingest([FixtureDocument("one", "Native parser evidence")])
        assert value.retrieve("Parser")[0][0].id == "one"
    finally:
        value.cleanup()


def test_benchmark_does_not_use_network_or_production_db(provider, tmp_path, monkeypatch):
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("no network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    live = tmp_path / "untouched-production.db"
    live.write_bytes(b"synthetic production sentinel")
    monkeypatch.setenv("DJOBS_DB", str(live))
    provider.ingest([FixtureDocument("one", "Parser privacy evidence")])
    assert provider.retrieve("Parser")[0]
    assert live.read_bytes() == b"synthetic production sentinel"
    assert "gold" not in json.dumps(provider.last_ingestion)
