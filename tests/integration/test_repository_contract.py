"""Repository contract tests — must pass for BOTH SQLite and PostgreSQL backends.

Each test is parameterised with a ``repo_factory`` fixture.
SQLite runs in-process; PostgreSQL is skipped if unavailable.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from djobs.core.models import Job
from djobs.core.states import JobStatus

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


def _sqlite_factory(tmp_path):
    from djobs.storage.sqlite import SQLiteJobRepository

    return SQLiteJobRepository.from_path(tmp_path / "contract.db")


# Cache the Postgres availability check for the whole test session. Without
# this, every parametrised [pg] variant would each wait out the full
# connect_timeout against an unreachable server — ~5s x N tests of pure idle
# waiting. We probe once: the first failure marks PG unavailable so all
# remaining pg variants skip instantly.
_PG_UNAVAILABLE_REASON: str | None = None
_PG_PROBED = False


def _pg_factory(_tmp_path):
    """Create a PostgresJobRepository, truncating tables between tests."""
    global _PG_PROBED, _PG_UNAVAILABLE_REASON
    import os

    require_pg = os.getenv("DJOBS_REQUIRE_PG") == "1"

    if require_pg:
        import psycopg
    else:
        psycopg = pytest.importorskip("psycopg")

    # Fast path: a previous test in this session already found PG unreachable.
    if not require_pg and _PG_PROBED and _PG_UNAVAILABLE_REASON is not None:
        pytest.skip(_PG_UNAVAILABLE_REASON)

    dsn = os.getenv(
        "DJOBS_TEST_PG_DSN",
        "postgresql://djobs:djobs@localhost:5432/djobs",
    )
    try:
        from psycopg.rows import dict_row

        # connect_timeout bounds the wait so a missing/unreachable server fails
        # fast (and skips) instead of hanging the whole suite. CI's test-postgres
        # job sets DJOBS_REQUIRE_PG=1 and provides a real server.
        conn = psycopg.connect(dsn, row_factory=dict_row, connect_timeout=5)
    except psycopg.OperationalError as exc:
        _PG_PROBED = True
        if require_pg:
            raise
        _PG_UNAVAILABLE_REASON = f"PostgreSQL not available: {exc}"
        pytest.skip(_PG_UNAVAILABLE_REASON)

    _PG_PROBED = True

    from djobs.storage.postgres import PG_SCHEMA_SQL, PostgresJobRepository

    with conn.cursor() as cur:
        cur.execute(PG_SCHEMA_SQL)
    conn.commit()
    # Typed canonical tables do not depend on observation rows; reset this
    # explicitly isolated contract fixture without changing runtime deletion rules.
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('memory_artifacts') AS name")
        if cur.fetchone()["name"]:
            cur.execute("TRUNCATE memory_artifacts CASCADE")
    conn.commit()
    # Truncate for a clean slate
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE djobs_diagnostics, agent_observations, repository_snapshots, "
            "context_revisions, agents, job_events, jobs CASCADE"
        )
    conn.commit()
    return PostgresJobRepository(conn)


@pytest.fixture(params=["sqlite", "pg"])
def repo(request, tmp_path):
    repository = _sqlite_factory(tmp_path) if request.param == "sqlite" else _pg_factory(tmp_path)
    try:
        yield repository
    finally:
        if hasattr(repository, "_conn"):
            repository._conn.rollback()
            repository._conn.close()
        else:
            repository.close()


# ------------------------------------------------------------------
# Contract tests
# ------------------------------------------------------------------


def test_create_and_get(repo) -> None:
    job = Job(type="demo", payload={"k": "v"}, max_attempts=3)
    created = repo.create_job(job)
    assert created.id == job.id

    fetched = repo.get_job(job.id)
    assert fetched is not None
    assert fetched.type == "demo"
    assert fetched.payload == {"k": "v"}
    assert fetched.status == JobStatus.PENDING
    assert fetched.correlation_id == job.correlation_id


def test_claim_marks_running(repo) -> None:
    repo.create_job(Job(type="a"))
    claimed = repo.claim_next_job("w-1")
    assert claimed is not None
    assert claimed.status == JobStatus.RUNNING
    assert claimed.attempt == 1
    assert claimed.leased_by == "w-1"
    assert claimed.started_at is not None


def test_claim_returns_none_when_empty(repo) -> None:
    assert repo.claim_next_job("w-1") is None


def test_future_run_after_not_claimed(repo) -> None:
    far = datetime.now(timezone.utc) + timedelta(hours=1)
    repo.create_job(Job(type="a", run_after=far))
    assert repo.claim_next_job("w-1") is None


def test_mark_succeeded(repo) -> None:
    repo.create_job(Job(type="a"))
    repo.claim_next_job("w-1")
    job = repo.get_job(repo.list_events()[0].job_id)
    succeeded = repo.mark_succeeded(job.id)
    assert succeeded.status == JobStatus.SUCCEEDED
    assert succeeded.leased_by is None


def test_mark_failed(repo) -> None:
    repo.create_job(Job(type="a"))
    claimed = repo.claim_next_job("w-1")
    failed = repo.mark_failed(claimed.id, "oops")
    assert failed.status == JobStatus.FAILED
    assert failed.last_error == "oops"


def test_mark_retry_scheduled(repo) -> None:
    repo.create_job(Job(type="a", max_attempts=3))
    claimed = repo.claim_next_job("w-1")
    run_after = datetime.now(timezone.utc) + timedelta(seconds=10)
    retried = repo.mark_retry_scheduled(claimed.id, "temp", run_after)
    assert retried.status == JobStatus.RETRY_SCHEDULED
    assert retried.run_after is not None


def test_promote_due_retries(repo) -> None:
    repo.create_job(Job(type="a", max_attempts=3))
    claimed = repo.claim_next_job("w-1")
    past = datetime.now(timezone.utc) - timedelta(seconds=10)
    repo.mark_retry_scheduled(claimed.id, "temp", past)

    promoted = repo.promote_due_retries()
    assert len(promoted) == 1
    assert promoted[0].status == JobStatus.PENDING


def test_mark_dead_lettered(repo) -> None:
    repo.create_job(Job(type="a"))
    claimed = repo.claim_next_job("w-1")
    dl = repo.mark_dead_lettered(claimed.id, "fatal")
    assert dl.status == JobStatus.DEAD_LETTERED


def test_heartbeat(repo) -> None:
    import time

    repo.create_job(Job(type="a"))
    claimed = repo.claim_next_job("w-1")
    old_expires = claimed.lease_expires_at
    time.sleep(0.01)  # ensure clock advances past claim timestamp
    hb = repo.heartbeat(claimed.id, "w-1")
    assert hb.lease_expires_at > old_expires


def test_recover_expired_leases(repo) -> None:
    repo.create_job(Job(type="a", max_attempts=3))
    repo.claim_next_job("w-crash", lease_duration=timedelta(seconds=1))

    far = datetime.now(timezone.utc) + timedelta(hours=1)
    recovered = repo.recover_expired_leases(far)
    assert len(recovered) == 1
    assert recovered[0].status == JobStatus.PENDING
    assert recovered[0].leased_by is None


def test_idempotency_key_dedupe(repo) -> None:
    j1 = Job(type="a", idempotency_key="ik-1")
    repo.create_job(j1)
    found = repo.find_active_by_idempotency_key("ik-1")
    assert found is not None
    assert found.id == j1.id


def test_count_by_status(repo) -> None:
    repo.create_job(Job(type="a"))
    repo.create_job(Job(type="b"))
    repo.claim_next_job("w-1")
    counts = repo.count_by_status()
    assert counts.get("pending", 0) == 1
    assert counts.get("running", 0) == 1


def test_count_running_by_type(repo) -> None:
    repo.create_job(Job(type="email"))
    repo.create_job(Job(type="sms"))
    repo.claim_next_job("w-1")  # claims email (oldest)
    assert repo.count_running_by_type("email") == 1
    assert repo.count_running_by_type("sms") == 0


def test_type_concurrency_limits(repo) -> None:
    repo.create_job(Job(type="email"))
    repo.create_job(Job(type="email"))
    repo.create_job(Job(type="sms"))

    j1 = repo.claim_next_job("w-1", type_concurrency_limits={"email": 1})
    assert j1 is not None
    assert j1.type == "email"

    # email at limit → should get sms
    j2 = repo.claim_next_job("w-2", type_concurrency_limits={"email": 1})
    assert j2 is not None
    assert j2.type == "sms"


def test_resource_key_exclusive_lock(repo) -> None:
    repo.create_job(Job(type="edit", resource_key="file.py"))
    repo.create_job(Job(type="edit", resource_key="file.py"))

    # First claim takes the resource lock on file.py.
    j1 = repo.claim_next_job("w-1")
    assert j1 is not None
    assert j1.resource_key == "file.py"

    # Second job shares the same resource_key → not claimable while j1 runs.
    j2 = repo.claim_next_job("w-2")
    assert j2 is None

    # After j1 finishes, the resource is free again.
    repo.mark_succeeded(j1.id)
    j3 = repo.claim_next_job("w-3")
    assert j3 is not None
    assert j3.resource_key == "file.py"


def test_depends_on_blocks_until_satisfied(repo) -> None:
    dep = Job(type="build")
    repo.create_job(dep)
    blocked = Job(type="test", depends_on=[dep.id])
    repo.create_job(blocked)

    # The dependent job is not claimable until its dependency succeeds.
    # Claim the dependency first (oldest), then the dependent stays blocked.
    j1 = repo.claim_next_job("w-1")
    assert j1 is not None
    assert j1.id == dep.id

    # Dependency still running → dependent not claimable.
    assert repo.claim_next_job("w-2") is None

    # Dependency succeeds → dependent becomes claimable.
    repo.mark_succeeded(dep.id)
    j2 = repo.claim_next_job("w-3")
    assert j2 is not None
    assert j2.id == blocked.id


def test_events_recorded(repo) -> None:
    job = Job(type="a")
    repo.create_job(job)
    repo.claim_next_job("w-1")
    repo.mark_succeeded(job.id)

    events = repo.list_events(job.id)
    types = [e.event_type for e in events]
    assert types == ["job_created", "job_claimed", "job_succeeded"]


def test_passive_memory_adapter_contract(repo) -> None:
    from djobs.storage.memory import memory_repository

    adapter = memory_repository(repo)
    scope = "contract-memory"
    now = datetime.now(timezone.utc).isoformat()

    def observation(memory_id: str, summary: str) -> dict[str, object]:
        return {
            "id": memory_id,
            "correlation_id": scope,
            "agent_type": "contract",
            "session_id_hash": "session",
            "event_type": "tool_result",
            "tool_name": "edit",
            "summary": summary,
            "metadata_json": json.dumps({"memory_status": "active"}),
            "created_at": now,
        }

    first_id = uuid.uuid4().hex
    second_id = uuid.uuid4().hex
    assert (
        adapter.insert_observation(
            observation(first_id, "same result"),
            marker_event="context_injected",
            max_observations=100,
            max_markers=20,
        )
        == first_id
    )
    adapter.insert_observation(
        observation(second_id, "same result"),
        marker_event="context_injected",
        max_observations=100,
        max_markers=20,
    )

    unique_id = uuid.uuid4().hex
    unique = observation(unique_id, "unique result")
    assert (
        adapter.insert_unique_observation(
            unique,
            scopes=(scope,),
            marker_event="context_injected",
            max_observations=100,
            max_markers=20,
        )
        is True
    )
    duplicate_unique = observation(uuid.uuid4().hex, "unique result")
    assert (
        adapter.insert_unique_observation(
            duplicate_unique,
            scopes=(scope,),
            marker_event="context_injected",
            max_observations=100,
            max_markers=20,
        )
        is False
    )
    assert adapter.observation_metadata(memory_id=unique_id, scopes=(scope,)) is not None
    assert (
        adapter.update_observation_metadata(
            memory_id=unique_id,
            metadata_json=json.dumps({"memory_status": "resolved"}),
        )
        is True
    )
    session = adapter.session_rows(scopes=(scope,), session_hash="session", limit=10)
    assert any(str(row["id"]) == unique_id for row in session)
    assert adapter.forget(memory_id=unique_id, scopes=(scope,)) is True

    assert (
        adapter.upsert_snapshot(
            checkout_id="checkout-contract",
            digest="first",
            summary="first snapshot",
            updated_at=now,
            observation=None,
            record_initial=False,
            marker_event="context_injected",
            max_observations=100,
            max_markers=20,
        )
        is False
    )
    assert (
        adapter.upsert_snapshot(
            checkout_id="checkout-contract",
            digest="second",
            summary="second snapshot",
            updated_at=now,
            observation=None,
            record_initial=False,
            marker_event="context_injected",
            max_observations=100,
            max_markers=20,
        )
        is True
    )

    rows = adapter.recent_rows(scopes=(scope,), marker_event="context_injected", limit=10)
    assert {str(row["id"]) for row in rows} == {first_id, second_id}
    assert adapter.stats(scopes=(scope,))["total"] == 2
    preview = adapter.compact(scopes=(scope,), keep_recent=1, dry_run=True)
    applied = adapter.compact(scopes=(scope,), keep_recent=1, dry_run=False)
    assert preview["duplicates"] == 1
    assert applied == preview
    assert (
        len(adapter.recent_rows(scopes=(scope,), marker_event="context_injected", limit=10)) == 1
    )


def test_diagnostics_adapter_contract(repo) -> None:
    from djobs.storage.diagnostics import clear, list_recent, record

    record(
        repo,
        component="contract.component",
        error_type="ContractError",
        message="redacted fixture",
        context={"phase": "first"},
    )
    record(
        repo,
        component="contract.component",
        error_type="ContractError",
        message="redacted fixture",
        context={"phase": "second"},
    )

    rows = list_recent(repo, limit=10)
    assert len(rows) == 1
    assert rows[0]["occurrence_count"] == 2
    assert rows[0]["context"] == {"phase": "second"}
    assert clear(repo) == 1
    assert list_recent(repo, limit=10) == []


def test_workspace_adapter_explicit_claim_contract(repo) -> None:
    from djobs.storage.workspace import workspace_repository

    task = Job(type="coding-checkpoint", correlation_id="contract-workspace")
    repo.create_job(task)
    adapter = workspace_repository(repo)

    state, claimed = adapter.claim_exact(
        task_id=task.id,
        agent_id="agent-contract",
        agent_type="contract",
        lease_seconds=120,
    )

    assert state == "claimed"
    assert claimed is not None
    assert claimed["status"] == "running"
    assert claimed["leased_by"] == "agent-contract"
    owned = adapter.owned_rows(
        correlation_ids=("contract-workspace",),
        job_types=("coding-checkpoint",),
        agent_id="agent-contract",
    )
    assert [str(row["id"]) for row in owned] == [task.id]


# Native memory indexes share the repository contract. These tests exercise the
# same SQL and lifecycle boundaries against SQLite and an isolated PG fixture.
def _memory_scope():
    from djobs.workspace import Workspace

    return Workspace(
        root=".",
        workspace_id="repo:memory-contract",
        checkout_id="repo:memory-contract",
        repo_family_id="family:memory-contract",
        correlation_ids=("repo:memory-contract",),
        memory_correlation_ids=("family:memory-contract", "repo:memory-contract"),
        source="fixture",
    )


class _MemoryEmbeddingStub:
    from djobs.embedding import EmbeddingIdentity

    identity = EmbeddingIdentity("contract-stub", "constant", "1", 2)

    def embed(self, texts, *, purpose):
        return [[1.0, 0.0] for _ in texts]


def test_memory_hybrid_index_contract(repo):
    from types import SimpleNamespace

    from djobs.embedding import EmbeddingSession
    from djobs.observations import record_observation
    from djobs.retrieval import reindex_memory, retrieve_memory
    from djobs.storage.retrieval import RetrievalIndex

    workspace = _memory_scope()
    agent = SimpleNamespace(agent_type="contract", session_id="memory-contract")
    record_observation(repo, workspace, agent, "tool_result", "src/parser.py preserves plus signs")
    record_observation(
        repo,
        workspace,
        agent,
        "tool_result",
        "quarantined parser claims",
        metadata={"memory_status": "imported_unverified"},
    )
    before = RetrievalIndex(repo).source_rows(workspace.memory_correlation_ids)
    session = EmbeddingSession(_MemoryEmbeddingStub())
    assert reindex_memory(repo, workspace, session)["status"] == "ready"
    assert reindex_memory(repo, workspace, session)["status"] == "unchanged"
    selected = retrieve_memory(repo, workspace, "parser", embedding=session, explain=True)
    assert selected.trace["semantic_index_status"] == "ready"
    assert len(selected.items) == 1
    assert selected.items[0]["summary"] == "src/parser.py preserves plus signs"
    assert RetrievalIndex(repo).source_rows(workspace.memory_correlation_ids) == before


def test_memory_forget_and_scope_contract(repo):
    from types import SimpleNamespace

    from djobs.embedding import EmbeddingSession
    from djobs.observations import clear_workspace_memory, forget_observation, record_observation
    from djobs.retrieval import reindex_memory, retrieve_memory
    from djobs.storage.retrieval import RetrievalIndex

    workspace = _memory_scope()
    agent = SimpleNamespace(agent_type="contract", session_id="forget-contract")
    record_observation(repo, workspace, agent, "tool_result", "src/parser.py shared rule")
    record_observation(
        repo,
        workspace,
        agent,
        "tool_result",
        "src/parser.py private rule",
        metadata={"scope": "checkout", "checkout_id": "repo:another"},
    )
    session = EmbeddingSession(_MemoryEmbeddingStub())
    assert reindex_memory(repo, workspace, session)["ok"]
    result = retrieve_memory(repo, workspace, "parser", embedding=session)
    assert len(result.items) == 1
    memory_id = result.items[0]["id"]
    assert forget_observation(repo, workspace, memory_id)
    index = RetrievalIndex(repo)
    with index._read_cursor() as cursor:
        cursor.execute(
            index._sql("SELECT COUNT(*) AS n FROM memory_embeddings WHERE record_id=?"),
            (memory_id,),
        )
        assert cursor.fetchone()["n"] == 0
    assert not retrieve_memory(repo, workspace, "parser", embedding=session).items
    clear_workspace_memory(repo, workspace)
    with index._read_cursor() as cursor:
        cursor.execute(
            index._sql(
                "SELECT COUNT(*) AS n FROM memory_embedding_indexes WHERE repo_family_id=?"
            ),
            (workspace.repo_family_id,),
        )
        assert cursor.fetchone()["n"] == 0


def test_typed_temporal_review_and_forget_contract(repo):
    from types import SimpleNamespace

    from djobs.artifacts import ArtifactMemory
    from djobs.memory_review import ReviewGate
    from djobs.observations import forget_observation, record_observation
    from djobs.storage.memory import memory_repository

    workspace = _memory_scope()
    agent = SimpleNamespace(agent_type="contract", session_id="typed-memory-contract")
    record_observation(repo, workspace, agent, "tool_result", "Persistence uses SQLite.")
    record_observation(
        repo, workspace, agent, "tool_result", "Persistence migrated to PostgreSQL."
    )
    sources = memory_repository(repo).scan_rows(
        scopes=workspace.memory_correlation_ids, marker_event="context_injected", limit=10
    )
    memory = ArtifactMemory(repo, workspace)
    accepted = []
    gate = ReviewGate(lambda request: "accept", reviewer="synthetic-contract-reviewer")
    for text, instant in (
        ("Persistence uses SQLite.", "2020-01-01T00:00:00Z"),
        ("Persistence migrated to PostgreSQL.", "2021-01-01T00:00:00Z"),
    ):
        source_id = next(row["id"] for row in sources if row["summary"] == text)
        artifact = memory.propose(
            {
                "kind": "fact",
                "title": "Persistence",
                "abstract": text,
                "sources": [source_id],
                "valid_from": instant,
            }
        )["artifact"]
        assert memory.review(artifact["id"], gate)["activated"]
        accepted.append((artifact["id"], source_id))
    old, new = accepted
    assert memory.relate(new[0], old[0], "supersedes", at="2021-01-01T00:00:00Z", gate=gate)[
        "changed"
    ]
    assert [item["id"] for item in memory.list_artifacts()["memories"]] == [new[0]]
    assert [
        item["id"] for item in memory.list_artifacts(at="2020-06-01T00:00:00Z")["memories"]
    ] == [old[0]]
    assert forget_observation(repo, workspace, new[1])
    assert memory.list_artifacts()["memories"] == []
    assert memory.get(old[0])["status"] == "superseded"


def test_typed_contradiction_and_compaction_contract(repo):
    from types import SimpleNamespace

    from djobs.artifacts import ArtifactMemory
    from djobs.memory_review import ReviewGate
    from djobs.observations import record_observation
    from djobs.storage.memory import memory_repository

    workspace = _memory_scope()
    agent = SimpleNamespace(agent_type="contract", session_id="typed-conflict-contract")
    memory = ArtifactMemory(repo, workspace)
    gate = ReviewGate(lambda request: "accept", reviewer="synthetic-contract-reviewer")
    ids = []
    adapter = memory_repository(repo)
    for text in ("Billing owns account deletion", "Accounts owns account deletion"):
        record_observation(repo, workspace, agent, "tool_result", text)
        original = next(
            row
            for row in adapter.scan_rows(
                scopes=workspace.memory_correlation_ids, marker_event="context_injected", limit=10
            )
            if row["summary"] == text
        )
        artifact = memory.propose(
            {
                "kind": "fact",
                "title": "Ownership",
                "abstract": text,
                "sources": [original["id"]],
                "valid_from": "2020-01-01T00:00:00Z",
            }
        )["artifact"]
        memory.review(artifact["id"], gate)
        ids.append(artifact["id"])
        record_observation(repo, workspace, agent, "tool_result", text)
    preview = adapter.compact(scopes=workspace.memory_correlation_ids, keep_recent=1, dry_run=True)
    assert preview["protected_sources"] == 2
    assert preview["total"] == 0
    memory.relate(ids[0], ids[1], "contradicts", at="2021-01-01T00:00:00Z", gate=gate)
    result = memory.list_artifacts(query="Billing")
    assert result["ambiguous"] and not result["memories"]


def test_forgotten_conflict_requires_new_review_contract(repo):
    from types import SimpleNamespace

    from djobs.artifacts import ArtifactMemory
    from djobs.memory_review import ReviewGate
    from djobs.observations import forget_observation, record_observation
    from djobs.storage.memory import memory_repository

    workspace = _memory_scope()
    agent = SimpleNamespace(agent_type="contract", session_id="forgotten-conflict")
    memory = ArtifactMemory(repo, workspace)
    gate = ReviewGate(lambda request: "accept", reviewer="synthetic-contract-reviewer")
    ids, originals = [], []
    for text in ("Billing owns deletion", "Accounts owns deletion"):
        record_observation(repo, workspace, agent, "tool_result", text)
        source = next(
            row["id"]
            for row in memory_repository(repo).scan_rows(
                scopes=workspace.memory_correlation_ids, marker_event="context_injected", limit=10
            )
            if row["summary"] == text
        )
        item = memory.propose(
            {
                "kind": "fact",
                "title": "Ownership",
                "abstract": text,
                "sources": [source],
                "valid_from": "2020-01-01T00:00:00Z",
            }
        )["artifact"]["id"]
        memory.review(item, gate)
        ids.append(item)
        originals.append(source)
    memory.relate(ids[0], ids[1], "contradicts", at="2021-01-01T00:00:00Z", gate=gate)
    before = memory.get(ids[1])["content_hash"]
    assert forget_observation(repo, workspace, originals[0])
    assert not memory.list_artifacts()["memories"]
    pending = memory.get(ids[1], depth=2)
    assert pending["status"] == "candidate" and pending["content_hash"] == before
    assert ids[0] not in json.dumps(pending)
    assert memory.review(ids[1], gate)["activated"]
    assert memory.list_artifacts()["count"] == 1


def test_capsule_copy_forget_contract(repo):
    from types import SimpleNamespace

    from djobs.artifacts import ArtifactMemory
    from djobs.memory_review import ReviewGate
    from djobs.observations import forget_observation, record_observation, record_session_capsule
    from djobs.storage.memory import memory_repository

    workspace = _memory_scope()
    agent = SimpleNamespace(agent_type="contract", session_id="copied-context")
    phrase = "SYNTHETIC ERASABLE EVIDENCE"
    record_observation(repo, workspace, agent, "tool_result", phrase)
    record_session_capsule(repo, workspace, agent, reason="contract")
    rows = memory_repository(repo).scan_rows(
        scopes=workspace.memory_correlation_ids, marker_event="context_injected", limit=10
    )
    raw = next(row["id"] for row in rows if row["event_type"] == "tool_result")
    capsule = next(row["id"] for row in rows if row["event_type"] == "session_capsule")
    memory = ArtifactMemory(repo, workspace)
    item = memory.propose(
        {"kind": "fact", "title": "Copied evidence", "abstract": phrase, "sources": [capsule]}
    )["artifact"]["id"]
    memory.review(
        item, ReviewGate(lambda request: "accept", reviewer="synthetic-contract-reviewer")
    )
    assert forget_observation(repo, workspace, raw)
    assert (
        memory_repository(repo).scan_rows(
            scopes=workspace.memory_correlation_ids, marker_event="context_injected", limit=10
        )
        == []
    )
    assert not memory.list_artifacts(exposure="audit")["memories"]


def test_passive_pg_read_does_not_leak_or_commit_outer_transaction(repo):
    if not hasattr(repo, "_conn"):
        return
    from psycopg.pq import TransactionStatus

    from djobs.storage.memory import memory_repository

    adapter = memory_repository(repo)
    workspace = _memory_scope()
    assert repo._conn.info.transaction_status == TransactionStatus.IDLE
    adapter.scan_rows(
        scopes=workspace.memory_correlation_ids, marker_event="context_injected", limit=10
    )
    assert repo._conn.info.transaction_status == TransactionStatus.IDLE
    repo._conn.execute("CREATE TEMP TABLE caller_transaction(n INTEGER)")
    repo._conn.execute("INSERT INTO caller_transaction VALUES (7)")
    adapter.stats(scopes=workspace.memory_correlation_ids)
    assert repo._conn.info.transaction_status == TransactionStatus.INTRANS
    repo._conn.rollback()
    assert (
        repo._conn.execute("SELECT to_regclass('caller_transaction') AS t").fetchone()["t"] is None
    )
    repo._conn.rollback()


def test_verified_learning_workflow_contract(repo):
    from scripts.benchmark_verified_learning import run

    result = run(repo)
    assert result["pass"]
    assert result["before"]["active_skills"] == 0
    assert result["after"]["active_skills"] == 1
    assert result["model_calls"] == 0


def test_context_projection_workflow_contract(repo):
    from scripts.benchmark_temporal_memory import run_projection

    result = run_projection(repo)
    assert result["pass"]
    assert result["after_project_selected"] == 5


def test_quarantined_session_portability_contract(repo):
    from scripts.benchmark_session_portability import run

    result = run(repo)
    assert result["pass"]
    assert result["after"]["imported_sessions"] == 3
    assert not result["native_resumption_supported"]


def test_external_candidate_boundary_contract(repo):
    from scripts.benchmark_external_memory import run

    result = run(repo)
    assert result["pass"]
    assert result["after"]["native_revalidated_candidates"] == 1
    assert result["after"]["rejected_candidates"] == 2


def test_concurrent_capsule_capture_does_not_restore_forgotten_source(repo, monkeypatch):
    import threading
    from types import SimpleNamespace

    import djobs.observations as observations
    from djobs.storage.memory import memory_repository

    workspace = _memory_scope()
    agent = SimpleNamespace(agent_type="contract", session_id="concurrent-capture")
    observations.record_observation(
        repo, workspace, agent, "tool_result", "Concurrent synthetic evidence"
    )
    original = memory_repository(repo).scan_rows(
        scopes=workspace.memory_correlation_ids, marker_event="context_injected", limit=10
    )[0]["id"]
    captured, resume = threading.Event(), threading.Event()
    read = observations.session_observations
    result = []

    def paused_read(*args, **kwargs):
        rows = read(*args, **kwargs)
        captured.set()
        assert resume.wait(5)
        return rows

    def capture():
        try:
            result.append(
                observations.record_session_capsule(repo, workspace, agent, reason="concurrent")
            )
        except Exception as exc:
            result.append(type(exc).__name__)

    monkeypatch.setattr(observations, "session_observations", paused_read)
    thread = threading.Thread(target=capture, daemon=True)
    thread.start()
    try:
        assert captured.wait(5)
        assert observations.forget_observation(repo, workspace, original)
    finally:
        resume.set()
        thread.join(5)
    assert not thread.is_alive() and result == [False]
    assert observations.recent_observations(repo, workspace) == []


def test_nested_observation_insert_preserves_caller_rollback_contract(repo):
    from types import SimpleNamespace

    import pytest

    from djobs.observations import record_observation
    from djobs.storage.artifacts import ArtifactStore
    from djobs.storage.memory import memory_repository

    workspace = _memory_scope()
    agent = SimpleNamespace(agent_type="contract", session_id="caller-owned-transaction")
    adapter = memory_repository(repo)
    adapter.ensure_schema()
    with (
        pytest.raises(RuntimeError, match="synthetic_rollback"),
        ArtifactStore(repo).transaction(write=True, family=workspace.repo_family_id),
    ):
        record_observation(repo, workspace, agent, "tool_result", "Must rollback together")
        raise RuntimeError("synthetic_rollback")
    assert (
        adapter.scan_rows(
            scopes=workspace.memory_correlation_ids, marker_event="context_injected", limit=10
        )
        == []
    )


@pytest.mark.parametrize("invalid_metadata", ["{broken", "[]", "null"])
def test_malformed_capsule_forget_rolls_back_contract(repo, invalid_metadata):
    from types import SimpleNamespace

    from djobs.memory_artifacts import ArtifactError
    from djobs.observations import forget_observation, record_observation
    from djobs.storage.artifacts import ArtifactStore
    from djobs.storage.memory import memory_repository

    workspace = _memory_scope()
    agent = SimpleNamespace(agent_type="contract", session_id="target-session")
    other = SimpleNamespace(agent_type="contract", session_id="copy-session")
    record_observation(repo, workspace, agent, "tool_result", "synthetic erasure target")
    record_observation(repo, workspace, other, "session_capsule", "corrupt copy")
    store = ArtifactStore(repo)
    with store.transaction(write=True, family=workspace.repo_family_id) as cursor:
        store.execute(
            cursor,
            "UPDATE agent_observations SET metadata_json=? WHERE event_type='session_capsule'",
            (invalid_metadata,),
        )
    adapter = memory_repository(repo)
    before = adapter.scan_rows(
        scopes=workspace.memory_correlation_ids, marker_event="context_injected", limit=10
    )
    original = next(row["id"] for row in before if row["event_type"] == "tool_result")
    with pytest.raises(ArtifactError, match="capsule_forget_provenance_invalid"):
        forget_observation(repo, workspace, original)
    assert (
        adapter.scan_rows(
            scopes=workspace.memory_correlation_ids, marker_event="context_injected", limit=10
        )
        == before
    )
