"""Typed memory contracts with synthetic source events and explicit test review."""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from djobs.artifacts import ArtifactMemory
from djobs.memory_artifacts import ArtifactDraft, ArtifactError, digest, timestamp
from djobs.memory_review import ReviewGate, ReviewRequest
from djobs.observations import record_observation
from djobs.storage.artifacts import forget_source_dependents, protected_observations
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.workspace import Workspace


@pytest.fixture
def env(tmp_path):
    repo = SQLiteJobRepository.from_path(tmp_path / "typed.db")
    workspace = Workspace(
        root=str(tmp_path),
        workspace_id="repo:a",
        checkout_id="repo:a",
        repo_family_id="family:test",
        correlation_ids=("repo:a",),
        memory_correlation_ids=("family:test", "repo:a"),
        source="fixture",
    )
    agent = SimpleNamespace(agent_type="test", session_id="session-a")
    yield repo, workspace, agent
    repo.close()


def source(env, summary="Canonical evidence", metadata=None):
    repo, workspace, _agent = env
    record_observation(repo, workspace, _agent, "tool_result", summary, metadata=metadata)
    return repo._connection.execute(
        "SELECT id FROM agent_observations WHERE summary=? ORDER BY created_at DESC LIMIT 1",
        (summary,),
    ).fetchone()[0]


def gate(decision="accept"):
    return ReviewGate(lambda request: decision, reviewer="synthetic-human-fixture")


def fact(
    memory, source_id, summary="Persistence uses SQLite.", start="2020-01-01T00:00:00Z", **kwargs
):
    result = memory.propose(
        {
            "kind": "fact",
            "title": "Persistence",
            "abstract": summary,
            "sources": [source_id],
            "valid_from": start,
            "observed_at": "2026-01-01T00:00:00Z",
            **kwargs,
        }
    )
    return result["artifact"]["id"]


def test_candidate_never_self_activates_and_acceptance_preserves_content(env):
    repo, workspace, _ = env
    memory = ArtifactMemory(repo, workspace)
    observation = source(env)
    item = fact(memory, observation)
    assert memory.list_artifacts()["memories"] == []
    assert memory.list_artifacts(exposure="candidates")["count"] == 1
    preview = memory.review(item)
    assert preview["requires_human_review"] and not preview["activated"]
    before = memory.get(item, depth=2)
    accepted = memory.review(item, gate())
    after = memory.get(item, depth=2)
    assert accepted["activated"]
    assert accepted["receipt"]["execution_authority"] is False
    assert before["content_hash"] == after["content_hash"]
    assert after["authority"] == "human_accepted"
    assert after["status"] == "active"
    assert memory.list_artifacts()["memories"][0]["id"] == item
    assert repo._connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    receipt = accepted["receipt"]
    assert receipt["receipt_hash"] == digest(
        {key: value for key, value in receipt.items() if key != "receipt_hash"}
    )


def test_reject_preserves_candidate_evidence_but_never_enters_resume(env):
    repo, workspace, _ = env
    memory = ArtifactMemory(repo, workspace)
    item = fact(memory, source(env))
    assert not memory.review(item, gate("reject"))["activated"]
    assert memory.get(item)["status"] == "rejected"
    assert memory.list_artifacts()["count"] == 0
    with pytest.raises(ArtifactError, match="not_pending"):
        memory.review(item, gate())


def test_temporal_supersession_preserves_historical_claim_and_sources(env):
    repo, workspace, _ = env
    memory = ArtifactMemory(repo, workspace)
    old = fact(memory, source(env, "Persistence uses SQLite."))
    new = fact(
        memory,
        source(env, "Persistence migrated to PostgreSQL."),
        "Persistence migrated to PostgreSQL.",
        "2021-01-01T00:00:00Z",
    )
    memory.review(old, gate())
    memory.review(new, gate())
    old_hash = memory.get(old)["content_hash"]
    preview = memory.relate(new, old, "supersedes", at="2021-01-01T00:00:00Z")
    assert not preview["changed"]
    result = memory.relate(new, old, "supersedes", at="2021-01-01T00:00:00Z", gate=gate())
    assert result["changed"]
    assert [row["id"] for row in memory.list_artifacts(kind="fact")["memories"]] == [new]
    historic = memory.list_artifacts(kind="fact", at="2020-06-01T00:00:00Z")
    assert [row["id"] for row in historic["memories"]] == [old]
    boundary = memory.list_artifacts(kind="fact", at="2021-01-01T00:00:00Z")
    assert [row["id"] for row in boundary["memories"]] == [new]
    audited = memory.get(old, depth=2)
    assert audited["content_hash"] == old_hash
    assert audited["status"] == "superseded"
    assert audited["valid_to"] == timestamp("2021-01-01T00:00:00Z")
    assert any(
        row["kind"] == "supersedes" and row["source_id"] == new for row in audited["relations"]
    )
    assert repo._connection.execute("SELECT COUNT(*) FROM agent_observations").fetchone()[0] == 2


def test_unresolved_contradiction_does_not_pick_a_winner_even_with_asymmetric_query(env):
    repo, workspace, _ = env
    memory = ArtifactMemory(repo, workspace)
    a = fact(memory, source(env, "Billing owns deletion"), "Billing owns deletion.")
    b = fact(memory, source(env, "Accounts owns deletion"), "Accounts owns deletion.")
    memory.review(a, gate())
    memory.review(b, gate())
    memory.relate(a, b, "contradicts", at="2021-01-01T00:00:00Z", gate=gate())
    for query in ("deletion", "Billing", "Accounts"):
        result = memory.list_artifacts(kind="fact", query=query)
        assert result["ambiguous"]
        assert result["memories"] == []
        assert set(result["conflicts"][0]) == {a, b}
    assert memory.list_artifacts(exposure="audit")["count"] == 2
    assert not memory.list_artifacts(at="2020-06-01T00:00:00Z")["ambiguous"]


def test_review_rechecks_exact_source_and_artifact_state_after_human_callback(env):
    repo, workspace, _ = env
    memory = ArtifactMemory(repo, workspace)
    observation = source(env)
    item = fact(memory, observation)

    def mutate_then_accept(request):
        repo.execute_write(
            "UPDATE agent_observations SET metadata_json=? WHERE id=?",
            (json.dumps({"memory_status": "stale"}), observation),
        )
        return "accept"

    with pytest.raises(ArtifactError, match="sources_unavailable"):
        memory.review(item, ReviewGate(mutate_then_accept, reviewer="synthetic-reviewer"))
    assert memory.get(item)["content_suppressed"]
    assert repo._connection.execute("SELECT COUNT(*) FROM memory_reviews").fetchone()[0] == 0


def test_review_approval_is_single_use_and_bound_to_issuer_and_content():
    first, second = gate(), gate()
    approval = first.request(ReviewRequest("review", "mem_test", "hash-one", "{}"))
    with pytest.raises(ArtifactError, match="invalid_review"):
        second.consume(approval, "hash-one")
    with pytest.raises(ArtifactError, match="invalid_review"):
        first.consume(approval, "hash-two")
    assert first.consume(approval, "hash-one") == "accept"
    with pytest.raises(ArtifactError, match="invalid_review"):
        first.consume(approval, "hash-one")


def test_any_missing_source_invalidates_claim_and_transitive_children(env):
    repo, workspace, _ = env
    memory = ArtifactMemory(repo, workspace)
    one, two = source(env, "one"), source(env, "two")
    parent = memory.propose(
        {
            "kind": "fact",
            "title": "Joint claim",
            "abstract": "Needs both sources",
            "sources": [one, two],
        }
    )["artifact"]["id"]
    memory.review(parent, gate())
    child = fact(memory, parent, "Dependent claim")
    memory.review(child, gate())
    repo.execute_write("DELETE FROM agent_observations WHERE id=?", (one,))
    assert memory.list_artifacts()["count"] == 0
    for item in (parent, child):
        read = memory.get(item, depth=2)
        assert read["content_suppressed"] and "abstract" not in read and "details" not in read


def test_deletion_helper_removes_content_and_protects_active_source_chain(env):
    repo, workspace, _ = env
    memory = ArtifactMemory(repo, workspace)
    observation = source(env)
    parent = fact(memory, observation)
    memory.review(parent, gate())
    child = fact(memory, parent, "Derived child")
    memory.review(child, gate())
    with repo.transaction(immediate=True) as cursor:
        assert observation in protected_observations(cursor, True)
        assert forget_source_dependents(cursor, True, [observation]) == 2
        cursor.execute("DELETE FROM agent_observations WHERE id=?", (observation,))
    assert repo._connection.execute("SELECT COUNT(*) FROM memory_artifacts").fetchone()[0] == 0
    assert repo._connection.execute("SELECT COUNT(*) FROM memory_reviews").fetchone()[0] == 0


def test_scope_cannot_widen_private_evidence_to_repository_family(env):
    repo, workspace, _agent = env
    private_source = source(
        env, "Checkout specific", {"scope": "checkout", "checkout_id": "repo:a"}
    )
    memory = ArtifactMemory(repo, workspace)
    with pytest.raises(ArtifactError, match="scope_widening"):
        fact(memory, private_source)
    item = fact(memory, private_source, scope="checkout")
    memory.review(item, gate())
    other = ArtifactMemory(repo, replace(workspace, workspace_id="repo:b", checkout_id="repo:b"))
    assert other.list_artifacts()["count"] == 0
    with pytest.raises(ArtifactError, match="not_found"):
        other.get(item)
    assert memory.list_artifacts()["count"] == 1


def test_session_memory_requires_matching_explicit_opt_in(env):
    repo, workspace, _ = env
    observation = source(env, "Private session evidence", {"scope": "session"})
    private = ArtifactMemory(repo, workspace, session="session-a", private=True)
    item = fact(private, observation, scope="session")
    private.review(item, gate())
    assert private.list_artifacts()["count"] == 1
    assert ArtifactMemory(repo, workspace, session="session-a").list_artifacts()["count"] == 0
    assert (
        ArtifactMemory(repo, workspace, session="session-b", private=True).list_artifacts()[
            "count"
        ]
        == 0
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("authority", "human_accepted"),
        ("status", "active"),
        ("model_confidence", 1),
        ("confirm", True),
    ],
)
def test_payload_flags_cannot_grant_review_authority(env, field, value):
    repo, workspace, _ = env
    payload = {
        "kind": "fact",
        "title": "Claim",
        "abstract": "Claim",
        "sources": [source(env)],
        field: value,
    }
    with pytest.raises(ArtifactError, match="unknown_artifact_fields"):
        ArtifactMemory(repo, workspace).propose(payload)


def test_canonical_schemas_are_not_created_by_reads_or_failed_proposals(env):
    repo, workspace, _ = env
    memory = ArtifactMemory(repo, workspace)
    before = repo._connection.total_changes
    assert memory.list_artifacts()["count"] == 0
    assert repo._connection.total_changes == before
    with pytest.raises(ArtifactError, match="source_not_found"):
        fact(memory, "nonexistent-source")
    assert (
        repo._connection.execute(
            "SELECT name FROM sqlite_master WHERE name='memory_artifacts'"
        ).fetchone()
        is None
    )


def test_future_schema_and_tampered_content_fail_closed(env):
    repo, workspace, _ = env
    memory = ArtifactMemory(repo, workspace)
    item = fact(memory, source(env))
    repo.execute_write(
        "UPDATE memory_artifacts SET abstract=? WHERE id=?", ("tampered text", item)
    )
    assert memory.get(item)["content_suppressed"]
    repo.execute_write("UPDATE djobs_memory_schema SET version=999 WHERE component='artifacts'")
    with pytest.raises(ArtifactError, match="unsupported_artifact_schema"):
        memory.list_artifacts()


def test_episode_is_deterministic_membership_not_a_verified_outcome(env):
    repo, workspace, _ = env
    memory = ArtifactMemory(repo, workspace)
    observation = source(env, "An agent claims success, without verification")
    episode = memory.episode([observation])["artifact"]
    assert episode["type"] == "episode" and episode["authority"] == "deterministic_derived"
    assert "success" not in episode["abstract"]
    assert "verified" not in episode["abstract"]
    with pytest.raises(ArtifactError, match="verified_type_constructor"):
        memory.propose(
            {
                "kind": "experience",
                "title": "Fake success",
                "abstract": "Agent said done",
                "sources": [observation],
            }
        )


def test_redaction_precedes_typed_storage_and_review(env):
    repo, workspace, _ = env
    observation = source(env)
    memory = ArtifactMemory(repo, workspace)
    result = memory.propose(
        {
            "kind": "fact",
            "title": "API_KEY=synthetic-never-display",
            "abstract": "Bearer synthetic-private-value",
            "sources": [observation],
            "details": {"password": "synthetic-never-display"},
        }
    )
    item = result["artifact"]["id"]
    assert "synthetic-never-display" not in json.dumps(memory.get(item, depth=2))
    preview = memory.review(item)
    assert "synthetic-private-value" not in json.dumps(preview)
    assert "<redacted>" in json.dumps(preview)


def test_bounded_drafts_reject_unknown_naive_and_oversized_content():
    for value in ("2020-01-01", "not-a-time"):
        with pytest.raises(ArtifactError):
            timestamp(value)
    with pytest.raises(ArtifactError):
        ArtifactDraft.parse({"kind": "fact", "title": "x", "abstract": "x", "sources": []})
    with pytest.raises(ArtifactError):
        ArtifactDraft.parse(
            {"kind": "fact", "title": "x" * 1000, "abstract": "x", "sources": ["id"]}
        )


def test_repeated_default_time_proposal_is_idempotent(env):
    repo, workspace, _ = env
    memory = ArtifactMemory(repo, workspace)
    payload = {
        "kind": "fact",
        "title": "Stable request",
        "abstract": "Same evidence",
        "sources": [source(env)],
    }
    first = memory.propose(payload)
    before = repo._connection.total_changes
    second = memory.propose(payload)
    assert second["duplicate"] is True
    assert second["artifact"]["id"] == first["artifact"]["id"]
    assert repo._connection.total_changes == before


def test_historical_descendant_uses_source_validity_at_requested_time(env):
    repo, workspace, _ = env
    memory = ArtifactMemory(repo, workspace)
    old = fact(memory, source(env, "SQLite source"))
    memory.review(old, gate())
    child = fact(memory, old, "Historical dependent configuration")
    memory.review(child, gate())
    new = fact(memory, source(env, "PostgreSQL source"), "PostgreSQL", "2021-01-01T00:00:00Z")
    memory.review(new, gate())
    memory.relate(new, old, "supersedes", at="2021-01-01T00:00:00Z", gate=gate())
    historical = memory.list_artifacts(at="2020-06-01T00:00:00Z")
    assert child in {item["id"] for item in historical["memories"]}
    assert child not in {item["id"] for item in memory.list_artifacts()["memories"]}


def test_descendant_cannot_bypass_unresolved_source_contradiction(env):
    repo, workspace, _ = env
    memory = ArtifactMemory(repo, workspace)
    one = fact(memory, source(env, "Billing ownership evidence"), "Billing ownership")
    two = fact(memory, source(env, "Accounts ownership evidence"), "Accounts ownership")
    for item in (one, two):
        memory.review(item, gate())
    child = fact(memory, one, "Derived notification configuration")
    memory.review(child, gate())
    memory.relate(one, two, "contradicts", at="2021-01-01T00:00:00Z", gate=gate())
    result = memory.list_artifacts(query="notification")
    assert child not in {item["id"] for item in result["memories"]}
    assert result["ambiguous"] is True


def test_artifact_read_transaction_keeps_one_snapshot_across_connections(env):
    from pathlib import Path

    from djobs.storage.artifacts import ArtifactStore

    repo, _workspace, _ = env
    observation = source(env)
    db = Path(repo._connection.execute("PRAGMA database_list").fetchone()[2])
    writer = SQLiteJobRepository.from_path(db)
    store = ArtifactStore(repo)
    try:
        with store.transaction() as cursor:
            before = store.observations(cursor, [observation])
            writer.execute_write(
                "UPDATE agent_observations SET summary=? WHERE id=?",
                ("new committed source content", observation),
            )
            after = store.observations(cursor, [observation])
            assert before == after
        with store.transaction() as cursor:
            assert store.observations(cursor, [observation]) != before
    finally:
        writer.close()


def test_concurrent_identical_proposals_make_one_record(env):
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    repo, workspace, _ = env
    payload = {
        "kind": "fact",
        "title": "Same proposal",
        "abstract": "Same claim",
        "sources": [source(env)],
    }
    db = Path(repo._connection.execute("PRAGMA database_list").fetchone()[2])
    connections = [SQLiteJobRepository.from_path(db) for _ in range(2)]
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda connection: ArtifactMemory(connection, workspace).propose(payload),
                    connections,
                )
            )
        assert len({result["artifact"]["id"] for result in results}) == 1
        assert sum(result["duplicate"] for result in results) == 1
    finally:
        for connection in connections:
            connection.close()


def test_tight_budget_preserves_unresolved_ambiguity():
    from djobs.memory import _bounded

    result = {
        "ok": True,
        "action": "facts",
        "ambiguous": True,
        "conflicts": [["a" * 200, "b" * 200] for _ in range(20)],
        "memories": [],
    }
    encoded = _bounded(result, 64)
    assert (len(encoded) + 3) // 4 <= 64
    assert json.loads(encoded)["ambiguous"] is True


def test_explicit_new_time_is_not_deduplicated_and_reject_is_not_resurrected(env):
    repo, workspace, _ = env
    memory = ArtifactMemory(repo, workspace)
    payload = {"kind": "fact", "title": "Proposal", "abstract": "Claim", "sources": [source(env)]}
    first = memory.propose(payload)["artifact"]["id"]
    memory.review(first, gate("reject"))
    repeated = memory.propose(payload)
    assert repeated["duplicate"] and repeated["artifact"]["status"] == "rejected"
    changed = memory.propose({**payload, "valid_from": "2021-01-01T00:00:00Z"})
    assert not changed["duplicate"] and changed["artifact"]["status"] == "candidate"


def test_temporal_workflow_benchmark_is_offline_and_reports_measured_checks(monkeypatch):
    import socket

    from scripts.benchmark_temporal_memory import run

    def forbidden(*args, **kwargs):
        raise AssertionError("No network is allowed")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    result = run()
    assert result["pass"]
    assert result["model_calls"] == 0
    assert result["answer_judge"] == "not_run"
    assert result["before"]["unrelated_persistence_claims"] == 2
    assert result["after"]["current_persistence_claims"] == 1
