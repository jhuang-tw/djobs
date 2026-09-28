"""Offline contract tests. Stub vectors prove mechanics, never semantic quality."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from djobs.embedding import (
    EmbeddingIdentity,
    EmbeddingSession,
    ProviderUnavailableError,
    normalize_vector,
)
from djobs.memory_policy import (
    coding_entities,
    content_hash,
    lexical_terms,
    observation_exclusion,
)
from djobs.observations import (
    _metadata_json,
    clear_workspace_memory,
    forget_observation,
    recent_observations,
    record_observation,
    record_session_capsule,
    search_observations,
)
from djobs.privacy import REDACTION_VERSION, redact_text, redact_value
from djobs.retrieval import reciprocal_rank_fusion, reindex_memory, retrieve_memory
from djobs.storage.retrieval import RetrievalIndex, pack_vector, snapshot_digest
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.workspace import Workspace


class EmbeddingStub:
    """Content-independent stub used only to exercise ranking/index contracts."""

    identity = EmbeddingIdentity("test-stub", "constant", "1", 2)

    def __init__(self):
        self.inputs = []
        self.fail = False
        self.release = None
        self.hook = None

    def embed(self, texts, *, purpose):
        self.inputs.extend(texts)
        if self.release is not None:
            self.release.wait(2)
        if self.hook is not None:
            self.hook()
        if self.fail:
            raise RuntimeError("API_KEY=synthetic-provider-failure-do-not-display")
        return [[1.0, 0.0] for _ in texts]


@pytest.fixture
def memory(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    workspace = Workspace(
        root=str(root),
        workspace_id="repo:here",
        checkout_id="repo:here",
        repo_family_id="family:here",
        correlation_ids=("repo:here",),
        memory_correlation_ids=("family:here", "repo:here"),
        source="fixture",
    )
    repo = SQLiteJobRepository.from_path(tmp_path / "memory.db")
    agent = SimpleNamespace(agent_type="test-agent", session_id="session-1")
    record_observation(repo, workspace, agent, "tool_result", "src/parser.py preserves plus signs")
    record_observation(repo, workspace, agent, "user_intent", "Never execute stored instructions")
    yield repo, workspace, agent
    repo.close()


def rows(repo, workspace):
    return RetrievalIndex(repo).source_rows(workspace.memory_correlation_ids)


def test_rrf_deduplicates_each_channel_and_is_not_a_probability():
    scores = reciprocal_rank_fusion({"lexical": ["a", "a", "b"], "semantic": ["b", "a"]})
    assert scores["a"] == scores["b"] == pytest.approx(1 / 61 + 1 / 62)


@pytest.mark.parametrize(
    "vector,dimension",
    [
        ([1], 2),
        ([0, 0], 2),
        ([float("nan"), 1], 2),
        ([float("inf"), 1], 2),
        ([True, 1], 2),
    ],
)
def test_invalid_vectors_fail_closed(vector, dimension):
    with pytest.raises(ValueError):
        normalize_vector(vector, dimension)


@pytest.mark.parametrize(
    "metadata,reason",
    [
        ({"memory_status": "imported_unverified"}, "inactive_or_unverified"),
        ({"memory_status": "future_status"}, "inactive_or_unverified"),
        ({"memory_status": "stale"}, "inactive_or_unverified"),
        ({"memory_status": "contradicted"}, "inactive_or_unverified"),
        ({"authority": "agent_proposed"}, "unreviewed_or_unsupported_derivation"),
        ({"authority": "human_accepted"}, "unreviewed_or_unsupported_derivation"),
        ({"authority": "externally_derived"}, "unreviewed_or_unsupported_derivation"),
        ({"scope": "checkout", "checkout_id": "repo:other"}, "wrong_checkout"),
        ({"scope": "session"}, "private_or_unknown_scope"),
        ({"scope": "agent"}, "private_or_unknown_scope"),
        ({"repo_family_id": "family:other"}, "wrong_repository"),
        ({"truncated_authority": True}, "incomplete_authority"),
        ({"superseded_by": "new"}, "superseded_or_contradicted"),
        ({"valid_to": "2020-01-01T00:00:00Z"}, "outside_validity_window"),
        ({"valid_from": "2099-01-01T00:00:00Z"}, "outside_validity_window"),
        ({"valid_from": "2020-01-01"}, "invalid_validity_window"),
    ],
)
def test_raw_metadata_cannot_grant_authority_or_escape_scope(memory, metadata, reason):
    _repo, workspace, _agent = memory
    row = {"metadata_json": metadata}
    assert (
        observation_exclusion(row, workspace, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        == reason
    )


def test_cjk_is_lexical_not_an_implicit_recent_query(memory):
    repo, workspace, agent = memory
    record_observation(repo, workspace, agent, "tool_result", "登入回呼必須保留加號")
    assert lexical_terms("回呼") == ("回呼",)
    assert "回呼" in search_observations(repo, workspace, "回呼")[0]["summary"]
    assert search_observations(repo, workspace, "完全無關的天文題目") == []


def test_missing_index_and_identity_change_fall_back_without_calling_provider(memory):
    repo, workspace, _agent = memory
    provider = EmbeddingStub()
    session = EmbeddingSession(provider)
    before = repo._connection.total_changes
    result = retrieve_memory(repo, workspace, "parser", embedding=session)
    assert result.items
    assert result.trace["fallback_reason"] == "index_missing"
    assert session.calls == 0
    assert repo._connection.total_changes == before
    assert reindex_memory(repo, workspace, session)["ok"]
    other = EmbeddingStub()
    other.identity = replace(other.identity, model_revision="2")
    changed = retrieve_memory(repo, workspace, "parser", embedding=EmbeddingSession(other))
    assert changed.trace["fallback_reason"] == "index_identity_mismatch"
    assert other.inputs == []


def test_explicit_reindex_is_atomic_idempotent_and_never_changes_observations_or_jobs(memory):
    repo, workspace, _agent = memory
    before = rows(repo, workspace)
    session = EmbeddingSession(EmbeddingStub())
    assert reindex_memory(repo, workspace, session)["status"] == "ready"
    calls = session.calls
    changes = repo._connection.total_changes
    assert reindex_memory(repo, workspace, session)["status"] == "unchanged"
    assert session.calls == calls
    assert repo._connection.total_changes == changes
    assert rows(repo, workspace) == before
    assert repo._connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    trace = retrieve_memory(repo, workspace, "parser", embedding=session, explain=True)
    assert trace.items[0]["retrieval"]["score_is_probability"] is False
    assert trace.trace["semantic_index_status"] == "ready"


def test_changed_source_disables_old_index_without_lazy_writes(memory):
    repo, workspace, agent = memory
    session = EmbeddingSession(EmbeddingStub())
    assert reindex_memory(repo, workspace, session)["ok"]
    record_observation(repo, workspace, agent, "tool_result", "new parser rule")
    changes = repo._connection.total_changes
    calls = session.calls
    result = retrieve_memory(repo, workspace, "parser", embedding=session)
    assert result.trace["fallback_reason"] == "index_stale"
    assert repo._connection.total_changes == changes
    assert session.calls == calls


def test_provider_failure_keeps_old_index_and_redacts_exceptions(memory):
    repo, workspace, agent = memory
    provider = EmbeddingStub()
    session = EmbeddingSession(provider)
    assert reindex_memory(repo, workspace, session)["ok"]
    previous = list(repo._connection.execute("SELECT * FROM memory_embeddings"))
    record_observation(repo, workspace, agent, "tool_result", "new parser source")
    provider.fail = True
    result = reindex_memory(repo, workspace, session)
    assert result["ok"] is False
    assert result["status"] == "provider_unavailable"
    assert "synthetic-provider" not in json.dumps(result)
    assert list(repo._connection.execute("SELECT * FROM memory_embeddings")) == previous


def test_provider_timeout_has_no_duplicate_request_or_late_database_effect(memory):
    repo, workspace, _agent = memory
    provider = EmbeddingStub()
    session = EmbeddingSession(provider, query_timeout_seconds=0.01)
    assert reindex_memory(repo, workspace, session)["ok"]
    provider.release = threading.Event()
    calls = session.calls
    before = repo._connection.total_changes
    started = time.monotonic()
    first = retrieve_memory(repo, workspace, "parser", embedding=session)
    second = retrieve_memory(repo, workspace, "parser", embedding=session)
    assert time.monotonic() - started < 0.5
    assert first.trace["fallback_reason"] == "provider_timeout"
    assert second.trace["fallback_reason"] == "provider_busy_after_timeout"
    assert session.calls == calls + 1
    assert first.items == second.items == search_observations(repo, workspace, "parser")
    provider.release.set()
    assert repo._connection.total_changes == before


def test_forget_and_clear_cascade_vectors_entities_without_touching_tasks(memory):
    repo, workspace, _agent = memory
    session = EmbeddingSession(EmbeddingStub())
    assert reindex_memory(repo, workspace, session)["ok"]
    source = next(row for row in rows(repo, workspace) if "parser" in row["summary"])
    assert forget_observation(repo, workspace, source["id"])
    assert (
        repo._connection.execute(
            "SELECT COUNT(*) FROM memory_embeddings WHERE record_id=?", (source["id"],)
        ).fetchone()[0]
        == 0
    )
    assert (
        repo._connection.execute(
            "SELECT COUNT(*) FROM memory_entity_links WHERE record_id=?", (source["id"],)
        ).fetchone()[0]
        == 0
    )
    assert (
        source["id"]
        not in retrieve_memory(repo, workspace, "parser", embedding=session).trace["selected_ids"]
    )
    clear_workspace_memory(repo, workspace)
    for table in ("memory_embeddings", "memory_entity_links", "memory_embedding_indexes"):
        assert repo._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_source_change_during_inference_rejects_entire_index_replacement(memory):
    repo, workspace, agent = memory
    provider = EmbeddingStub()
    provider.hook = lambda: record_observation(
        repo, workspace, agent, "tool_result", "concurrent change"
    )
    result = reindex_memory(repo, workspace, EmbeddingSession(provider))
    assert result["status"] == "sources_changed_during_reindex"
    assert RetrievalIndex(repo).status() == "missing"


def test_future_schema_and_corrupted_vector_fail_closed(memory):
    repo, workspace, _agent = memory
    session = EmbeddingSession(EmbeddingStub())
    assert reindex_memory(repo, workspace, session)["ok"]
    repo.execute_write("UPDATE memory_embeddings SET vector_bytes=?", (b"bad",))
    assert (
        retrieve_memory(repo, workspace, "parser", embedding=session).trace["fallback_reason"]
        == "index_corrupt"
    )
    repo.execute_write("UPDATE djobs_memory_schema SET version=999 WHERE component='retrieval'")
    before = repo._connection.total_changes
    assert reindex_memory(repo, workspace, session)["status"] == "unsupported_schema"
    assert (
        retrieve_memory(repo, workspace, "parser", embedding=session).trace["fallback_reason"]
        == "index_unsupported_schema"
    )
    assert repo._connection.total_changes == before


def test_partial_schema_write_rolls_back_and_can_be_retried(memory, monkeypatch):
    import djobs.storage.retrieval as storage

    repo, workspace, _agent = memory
    original = storage.MEMORY_RETRIEVAL_SCHEMA_SQL
    monkeypatch.setattr(storage, "MEMORY_RETRIEVAL_SCHEMA_SQL", original + ";INVALID SQL")
    session = EmbeddingSession(EmbeddingStub())
    assert not reindex_memory(repo, workspace, session)["ok"]
    assert RetrievalIndex(repo).status() == "missing"
    monkeypatch.setattr(storage, "MEMORY_RETRIEVAL_SCHEMA_SQL", original)
    assert reindex_memory(repo, workspace, session)["ok"]


def test_readonly_query_changes_no_db_bytes_mtime_or_sidecars(memory):
    repo, workspace, _agent = memory
    session = EmbeddingSession(EmbeddingStub())
    assert reindex_memory(repo, workspace, session)["ok"]
    db = Path(repo._connection.execute("PRAGMA database_list").fetchone()[2])
    repo._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    repo._connection.execute("PRAGMA journal_mode=DELETE")
    before = (hashlib.sha256(db.read_bytes()).hexdigest(), db.stat().st_mtime_ns)
    connection = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    readonly = SimpleNamespace(_connection=connection, _lock=threading.RLock())
    try:
        assert retrieve_memory(readonly, workspace, "parser", embedding=session).items
        assert connection.total_changes == 0
        assert before == (hashlib.sha256(db.read_bytes()).hexdigest(), db.stat().st_mtime_ns)
        assert not Path(str(db) + "-wal").exists()
        assert not Path(str(db) + "-shm").exists()
    finally:
        connection.close()


def test_redaction_precedes_storage_embedding_and_explanation(memory):
    repo, workspace, agent = memory
    secret = "synthetic-not-a-real-credential"
    record_observation(
        repo,
        workspace,
        agent,
        "tool_result",
        "parser API_KEY=" + secret,
        metadata={"nested": {"authorization": secret}, "cookies": secret},
    )
    stored = rows(repo, workspace)
    assert secret not in json.dumps(stored)
    session = EmbeddingSession(EmbeddingStub())
    assert reindex_memory(repo, workspace, session)["ok"]
    assert secret not in json.dumps(session.provider.inputs)
    result = retrieve_memory(
        repo, workspace, "parser API_KEY=" + secret, embedding=session, explain=True
    )
    assert secret not in json.dumps({"items": result.items, "trace": result.trace})
    assert "query" not in result.trace


def test_truncation_preserves_or_quarantines_all_security_fields(memory):
    _repo, workspace, _agent = memory
    for capsule in (False, True):
        metadata = {
            "authority": "agent_proposed",
            "scope": "checkout",
            "checkout_id": "other",
            "details": "large " * 2000,
            "goal": "large " * 2000,
        }
        if capsule:
            metadata["capsule_schema"] = 2
        encoded = _metadata_json(metadata, limit=240)
        assert len(encoded) <= 240
        assert observation_exclusion({"metadata_json": encoded}, workspace) is not None
        assert json.loads(encoded)["stored_as_data"] is True


def test_quarantined_session_cannot_be_laundered_into_a_capsule(memory):
    repo, workspace, agent = memory
    record_observation(
        repo,
        workspace,
        agent,
        "tool_result",
        "QUARANTINE UNIQUE FORBIDDEN",
        metadata={"memory_status": "imported_unverified"},
    )
    assert record_session_capsule(repo, workspace, agent, reason="test")
    assert "QUARANTINE" not in json.dumps(recent_observations(repo, workspace, limit=20))


def test_scope_is_enforced_for_recent_search_and_reindex(memory):
    repo, workspace, agent = memory
    record_observation(
        repo,
        workspace,
        agent,
        "user_intent",
        "PRIVATE CHECKOUT MATCH",
        metadata={"scope": "checkout", "checkout_id": "repo:other"},
    )
    session = EmbeddingSession(EmbeddingStub())
    assert reindex_memory(repo, workspace, session)["ok"]
    for result in (
        recent_observations(repo, workspace),
        search_observations(repo, workspace, "PRIVATE"),
        retrieve_memory(repo, workspace, "PRIVATE", embedding=session).items,
    ):
        assert "PRIVATE CHECKOUT" not in json.dumps(result)
    assert "PRIVATE CHECKOUT" not in json.dumps(session.provider.inputs)


def test_structured_secret_redaction_and_entity_linking_are_safe():
    secret = "synthetic-cookie-not-real"
    assert secret not in str(redact_value({"cookies": secret, "nested": {"password": secret}}))
    assert secret not in redact_text("Cookie: session=" + secret)
    opaque = "A8cD9eF1gH2iJ3kL4mN5oP6qR7sT8uV9"
    assert opaque not in redact_text(opaque)
    commit = "699db623da765c9db662f1cc391ffbf5348efec6"
    assert redact_text(commit) == commit
    entities = coding_entities("src/parser.py test_state UnicodeEncodeError " + commit)
    assert "file:src/parser.py" in entities
    assert "symbol:test_state" in entities
    assert "error:unicodeencodeerror" in entities
    assert content_hash({"summary": "a"}) != content_hash({"summary": "b"})
    assert REDACTION_VERSION in EmbeddingIdentity("stub", "stub", "1", 2).to_json()


def test_index_replacement_cannot_insert_an_unbound_record(memory):
    repo, workspace, _agent = memory
    index = RetrievalIndex(repo)
    source = rows(repo, workspace)
    with pytest.raises(ValueError, match="outside"):
        index.replace(
            family=workspace.repo_family_id,
            scopes=workspace.memory_correlation_ids,
            identity=EmbeddingStub.identity,
            sources=source,
            records=[("unknown", "fake", pack_vector((1.0, 0.0), 2), ())],
        )
    assert snapshot_digest(source) == snapshot_digest(list(reversed(source)))


def test_default_provider_contract_rejects_invalid_identity_and_dimension():
    with pytest.raises(ValueError):
        EmbeddingIdentity("stub", "model", "1", 0)
    with pytest.raises(ValueError):
        EmbeddingIdentity("stub", "model", "1", 2, redaction_version="old")
    provider = EmbeddingStub()
    session = EmbeddingSession(provider)
    provider.identity = replace(provider.identity, model_revision="2")
    with pytest.raises(ProviderUnavailableError, match="identity_changed"):
        session.embed(["safe"], purpose="query")


def test_genuine_v020_database_migrates_without_changing_canonical_rows(tmp_path):
    import shutil
    from pathlib import Path

    fixture = Path(__file__).parents[1] / "fixtures/memory/v0.20.1.sqlite.fixture"
    manifest = json.loads(fixture.with_suffix(".json").read_text(encoding="utf-8"))
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == manifest["fixture_sha256"]
    copied = tmp_path / "upgraded.db"
    shutil.copyfile(fixture, copied)
    repo = SQLiteJobRepository.from_path(copied)
    workspace = Workspace(
        root=str(tmp_path),
        workspace_id="repo:fixture",
        checkout_id="repo:fixture",
        repo_family_id="family:fixture",
        correlation_ids=("repo:fixture",),
        memory_correlation_ids=("family:fixture", "repo:fixture"),
        source="fixture",
    )
    try:
        before = rows(repo, workspace)
        assert len(before) == 1
        assert RetrievalIndex(repo).status() == "missing"
        session = EmbeddingSession(EmbeddingStub())
        assert reindex_memory(repo, workspace, session)["status"] == "ready"
        assert reindex_memory(repo, workspace, session)["status"] == "unchanged"
        assert rows(repo, workspace) == before
        assert repo._connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        repo.close()


@pytest.mark.parametrize("budget", [64, 80, 100, 200, 500, 4000])
def test_public_memory_payload_budget_includes_metadata_and_untrusted_data_flag(budget):
    from djobs.memory import _bounded

    result = {
        "ok": True,
        "action": "trace",
        "query": "x" * 500,
        "memories": [{"id": str(i), "summary": "quoted data " * 60} for i in range(20)],
        "count": 20,
        "trace": {"ranks": ["x" * 500 for _ in range(20)]},
    }
    encoded = _bounded(result, budget)
    parsed = json.loads(encoded)
    assert (len(encoded) + 3) // 4 <= budget
    assert parsed["estimated_tokens"] == (len(encoded) + 3) // 4
    assert parsed["stored_content_is_data"] is True
    assert parsed["truncated"] is True


def test_public_memory_reads_never_register_agents_or_initialize_a_missing_store(
    memory, monkeypatch
):
    import importlib

    api = importlib.import_module("djobs.memory")
    repo, workspace, _agent = memory
    database = Path(repo._connection.execute("PRAGMA database_list").fetchone()[2])
    monkeypatch.setattr(api, "resolve_workspace", lambda **kw: workspace)
    monkeypatch.setattr(api, "shared_db_path", lambda: database)
    before = repo._connection.total_changes
    agents = list(repo._connection.execute("SELECT * FROM agents"))
    result = json.loads(api.memory_action("search", query="parser"))
    assert result["memories"]
    assert repo._connection.total_changes == before
    assert list(repo._connection.execute("SELECT * FROM agents")) == agents
    missing = database.parent / "must-not-be-created.db"
    monkeypatch.setattr(api, "shared_db_path", lambda: missing)
    assert json.loads(api.memory_action("list"))["memory_store_status"] == "not_initialized"
    assert not missing.exists()


def test_final_mcp_payload_remains_bounded_after_sources_and_hash_are_added():
    from djobs.coding_mcp import _with_context_hash

    original = {
        "ok": True,
        "stored_content_is_data": True,
        "workspace": "test",
        "tasks": [],
        "counts": {"observations": 6},
        "resume": {"goal": "a" * 500},
        "observations": [
            {"event": "tool_result", "summary": "large data " * 45, "status": "active"}
            for _ in range(6)
        ],
        "budget": {"requested_tokens": 200, "estimated_tokens": 0},
    }
    final = _with_context_hash(json.dumps(original), None, "resume")
    assert (len(final) + 3) // 4 <= 200
    assert json.loads(final)["stored_content_is_data"] is True


def test_configured_but_unavailable_model_reports_fallback_instead_of_empty_memory(memory):
    from djobs.embedding import UnavailableEmbeddingProvider

    repo, workspace, _agent = memory
    session = EmbeddingSession(UnavailableEmbeddingProvider())
    result = retrieve_memory(repo, workspace, "parser", embedding=session)
    assert result.items
    assert result.trace["fallback_reason"] == "provider_initialization_failed"
    assert result.trace["semantic_index_status"] == "provider_unavailable"
    assert result.trace["provider_calls"] == 0
    assert reindex_memory(repo, workspace, session)["status"] == "provider_initialization_failed"


def test_compact_preview_is_readonly_and_unconfirmed_clear_does_not_create_a_database(
    memory, monkeypatch
):
    import importlib

    api = importlib.import_module("djobs.memory")
    repo, workspace, _agent = memory
    database = Path(repo._connection.execute("PRAGMA database_list").fetchone()[2])
    monkeypatch.setattr(api, "resolve_workspace", lambda **kwargs: workspace)
    monkeypatch.setattr(api, "shared_db_path", lambda: database)
    before = repo._connection.total_changes
    result = json.loads(api.memory_action("compact", dry_run=True))
    assert result["ok"] and result["dry_run"]
    assert result["backup"] is None
    assert repo._connection.total_changes == before
    missing = database.parent / "unconfirmed-do-not-create.db"
    monkeypatch.setattr(api, "shared_db_path", lambda: missing)
    assert json.loads(api.memory_action("clear"))["requires_confirmation"]
    assert not missing.exists()


def test_index_snapshot_tracks_temporal_and_session_provenance(memory):
    repo, workspace, _agent = memory
    source = rows(repo, workspace)
    for name in ("created_at", "agent_type", "session_id_hash"):
        changed = [dict(row) for row in source]
        changed[0][name] = "different"
        assert snapshot_digest(source) != snapshot_digest(changed)


def test_model_provisioning_is_explicit_hash_pinned_and_atomic(tmp_path, monkeypatch):
    import io

    import scripts.prepare_local_embedding as provision

    payload = b"synthetic-model-fixture"
    monkeypatch.setattr(
        provision,
        "FILES",
        {"onnx/model_quantized.onnx": (len(payload), hashlib.sha256(payload).hexdigest())},
    )
    calls = []

    class FakeOpener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            return io.BytesIO(payload)

    monkeypatch.setattr(provision.urllib.request, "build_opener", lambda *args: FakeOpener())
    destination = tmp_path / "model"
    manifest = provision.prepare(destination)
    assert manifest["model_revision"] == provision.MODEL_REVISION
    assert len(calls) == 1
    assert provision.MODEL_REVISION in calls[0]
    assert (destination / "manifest.json").is_file()
    with pytest.raises(ValueError, match="already exist"):
        provision.prepare(destination)
    monkeypatch.setattr(provision, "FILES", {"bad": (len(payload), "wrong-hash")})
    failed = tmp_path / "failed-model"
    with pytest.raises(ValueError, match="digest"):
        provision.prepare(failed)
    assert not failed.exists()
    assert not list(tmp_path.glob(".djobs-model-*"))


@pytest.mark.parametrize("live_writer", [False, True])
def test_real_wal_reads_preserve_source_bytes_sidecars_and_committed_content(
    tmp_path, live_writer
):
    from djobs.storage.read_only import connect_read_only

    database = tmp_path / "wal-source.db"
    writer = SQLiteJobRepository.from_path(database)
    writer.execute_write("CREATE TABLE snapshot_fixture(value TEXT)")
    writer.execute_write("INSERT INTO snapshot_fixture VALUES (?)", ("committed in WAL",))
    if not live_writer:
        writer.close()

    def fingerprint():
        return {
            p.name: (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in tmp_path.iterdir()
        }

    before = fingerprint()
    snapshot = connect_read_only(database)
    assert snapshot is not None
    temporary = Path(snapshot._temporary.name)
    try:
        assert (
            snapshot.execute("SELECT value FROM snapshot_fixture").fetchone()[0]
            == "committed in WAL"
        )
        with pytest.raises(sqlite3.OperationalError):
            snapshot.execute("DELETE FROM snapshot_fixture")
        assert before == fingerprint()
        if live_writer:
            writer.execute_write("INSERT INTO snapshot_fixture VALUES (?)", ("after snapshot",))
            assert snapshot.execute("SELECT COUNT(*) FROM snapshot_fixture").fetchone()[0] == 1
    finally:
        snapshot.close()
        if live_writer:
            writer.close()
    assert not temporary.exists()
    if not live_writer:
        assert before == fingerprint()


def test_snapshot_capture_detects_mutation_and_leaves_no_private_copy(tmp_path, monkeypatch):
    import djobs.storage.read_only as readonly

    database = tmp_path / "capture-race.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE marker(n INTEGER)")
    connection.commit()
    connection.close()
    original = readonly._copy_or_hash
    directories = []

    def change_after_copy(source, destination, *, deadline):
        digest = original(source, destination, deadline=deadline)
        if destination is not None:
            directories.append(destination.parent)
            database.touch()
        return digest

    monkeypatch.setattr(readonly, "_copy_or_hash", change_after_copy)
    with pytest.raises(readonly.SnapshotUnavailableError, match="source_changed"):
        readonly.connect_read_only(database)
    assert directories and all(not item.exists() for item in directories)


def test_snapshot_size_bound_is_explicit_unavailable_not_empty(tmp_path, monkeypatch):
    import djobs.storage.read_only as readonly

    database = tmp_path / "large.db"
    database.write_bytes(b"synthetic-size-bound-fixture")
    monkeypatch.setattr(readonly, "_MAX_BYTES", 8)
    with pytest.raises(readonly.SnapshotUnavailableError, match="capture_bound"):
        readonly.connect_read_only(database)
    assert readonly.connect_read_only(tmp_path / "missing.db") is None


def test_scope_restrictions_apply_to_audit_without_hiding_legitimate_history(memory):
    from djobs.memory_policy import scope_exclusion

    _repo, workspace, _agent = memory
    old = {
        "correlation_id": workspace.repo_family_id,
        "metadata_json": {"memory_status": "superseded"},
    }
    assert scope_exclusion(old, workspace) is None
    assert observation_exclusion(old, workspace) == "inactive_or_unverified"
    private = {
        "correlation_id": workspace.repo_family_id,
        "session_id_hash": "one",
        "metadata_json": {"scope": "session"},
    }
    assert scope_exclusion(private, workspace) == "private_or_unknown_scope"
    assert scope_exclusion(private, workspace, session_id_hash="two") == "private_or_unknown_scope"
    assert scope_exclusion(private, workspace, session_id_hash="one") is None
