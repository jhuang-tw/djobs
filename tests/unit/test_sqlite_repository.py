"""Tests for SQLiteJobRepository."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest

from djobs.core.errors import AgentNotFoundError, JobNotFoundError
from djobs.core.models import Job
from djobs.core.states import AgentStatus, JobStatus
from djobs.storage.sqlite import SQLiteJobRepository


def test_create_job_persists_job_and_event(tmp_path) -> None:
    repository = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    job = repository.create_job(Job(type="demo.echo", payload={"message": "hello"}))

    stored_job = repository.get_job(job.id)
    events = repository.list_events(job.id)

    assert stored_job is not None
    assert stored_job.type == "demo.echo"
    assert stored_job.payload == {"message": "hello"}
    assert stored_job.status == JobStatus.PENDING
    assert [event.event_type for event in events] == ["job_created"]


def test_claim_next_job_marks_running_and_records_event(tmp_path) -> None:
    repository = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    job = repository.create_job(Job(type="demo.echo"))

    claimed_job = repository.claim_next_job("worker-1")

    assert claimed_job is not None
    assert claimed_job.id == job.id
    assert claimed_job.status == JobStatus.RUNNING
    assert claimed_job.attempt == 1
    assert [event.event_type for event in repository.list_events(job.id)] == [
        "job_created",
        "job_claimed",
    ]


def test_claim_next_job_returns_none_when_empty(tmp_path) -> None:
    repository = SQLiteJobRepository.from_path(tmp_path / "jobs.db")

    assert repository.claim_next_job("worker-1") is None


def test_future_run_after_job_is_not_claimed(tmp_path) -> None:
    repository = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    future_time = datetime.now(UTC) + timedelta(hours=1)
    repository.create_job(Job(type="demo.echo", run_after=future_time))

    assert repository.claim_next_job("worker-1") is None


def test_mark_succeeded_updates_status_and_event(tmp_path) -> None:
    repository = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    job = repository.create_job(Job(type="demo.echo"))
    claimed_job = repository.claim_next_job("worker-1")

    assert claimed_job is not None
    succeeded_job = repository.mark_succeeded(claimed_job.id)

    assert succeeded_job.status == JobStatus.SUCCEEDED
    assert [event.event_type for event in repository.list_events(job.id)] == [
        "job_created",
        "job_claimed",
        "job_succeeded",
    ]


def test_mark_failed_updates_error_and_event(tmp_path) -> None:
    repository = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    job = repository.create_job(Job(type="demo.echo"))
    claimed_job = repository.claim_next_job("worker-1")

    assert claimed_job is not None
    failed_job = repository.mark_failed(claimed_job.id, "boom")

    assert failed_job.status == JobStatus.FAILED
    assert failed_job.last_error == "boom"
    events = repository.list_events(job.id)
    assert [event.event_type for event in events] == [
        "job_created",
        "job_claimed",
        "job_failed",
    ]
    assert events[-1].message == "boom"


def test_mark_retry_scheduled_updates_run_after_and_event(tmp_path) -> None:
    repository = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    job = repository.create_job(Job(type="demo.echo", max_attempts=2))
    claimed_job = repository.claim_next_job("worker-1")
    run_after = datetime.now(UTC) + timedelta(seconds=5)

    assert claimed_job is not None
    retry_job = repository.mark_retry_scheduled(claimed_job.id, "temporary outage", run_after)

    assert retry_job.status == JobStatus.RETRY_SCHEDULED
    assert retry_job.last_error == "temporary outage"
    assert retry_job.run_after == run_after
    assert retry_job.attempt == 1
    assert [event.event_type for event in repository.list_events(job.id)] == [
        "job_created",
        "job_claimed",
        "retry_scheduled",
    ]


def test_promote_due_retries_moves_job_back_to_pending(tmp_path) -> None:
    repository = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    job = repository.create_job(Job(type="demo.echo", max_attempts=2))
    claimed_job = repository.claim_next_job("worker-1")
    run_after = datetime.now(UTC) - timedelta(seconds=1)

    assert claimed_job is not None
    repository.mark_retry_scheduled(claimed_job.id, "temporary outage", run_after)
    promoted_jobs = repository.promote_due_retries(now=datetime.now(UTC))
    stored_job = repository.require_job(job.id)

    assert [promoted_job.id for promoted_job in promoted_jobs] == [job.id]
    assert stored_job.status == JobStatus.PENDING
    assert stored_job.run_after is None
    assert [event.event_type for event in repository.list_events(job.id)] == [
        "job_created",
        "job_claimed",
        "retry_scheduled",
        "retry_promoted",
    ]


def test_promote_due_retries_ignores_future_retry(tmp_path) -> None:
    repository = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    job = repository.create_job(Job(type="demo.echo", max_attempts=2))
    claimed_job = repository.claim_next_job("worker-1")
    run_after = datetime.now(UTC) + timedelta(minutes=10)

    assert claimed_job is not None
    repository.mark_retry_scheduled(claimed_job.id, "temporary outage", run_after)

    assert repository.promote_due_retries(now=datetime.now(UTC)) == []
    assert repository.require_job(job.id).status == JobStatus.RETRY_SCHEDULED


def test_mark_dead_lettered_updates_status_and_event(tmp_path) -> None:
    repository = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    job = repository.create_job(Job(type="demo.echo", max_attempts=1))
    claimed_job = repository.claim_next_job("worker-1")

    assert claimed_job is not None
    dead_lettered_job = repository.mark_dead_lettered(claimed_job.id, "retry budget exhausted")

    assert dead_lettered_job.status == JobStatus.DEAD_LETTERED
    assert dead_lettered_job.last_error == "retry budget exhausted"
    assert [event.event_type for event in repository.list_events(job.id)] == [
        "job_created",
        "job_claimed",
        "job_dead_lettered",
    ]


def test_release_job_returns_task_to_pending(tmp_path) -> None:
    repository = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    job = repository.create_job(Job(type="demo.echo"))
    claimed_job = repository.claim_next_job("agent-a")

    assert claimed_job is not None
    released_job = repository.release_job(job.id, "agent-a", reason="cannot proceed")

    assert released_job.status == JobStatus.PENDING
    assert released_job.leased_by is None
    assert released_job.lease_expires_at is None
    events = repository.list_events(job.id)
    assert [event.event_type for event in events] == [
        "job_created",
        "job_claimed",
        "job_released",
    ]
    assert events[-1].message == "cannot proceed"
    assert events[-1].metadata == {"worker_id": "agent-a"}


def test_released_job_can_be_reclaimed_by_another_agent(tmp_path) -> None:
    repository = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    job = repository.create_job(Job(type="demo.echo"))
    repository.claim_next_job("agent-a")
    repository.release_job(job.id, "agent-a")

    reclaimed = repository.claim_next_job("agent-b")

    assert reclaimed is not None
    assert reclaimed.id == job.id
    assert reclaimed.leased_by == "agent-b"


def test_release_job_rejects_wrong_owner(tmp_path) -> None:
    repository = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    job = repository.create_job(Job(type="demo.echo"))
    repository.claim_next_job("agent-a")

    with pytest.raises(JobNotFoundError):
        repository.release_job(job.id, "agent-b")


def test_release_job_rejects_unclaimed_task(tmp_path) -> None:
    repository = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    job = repository.create_job(Job(type="demo.echo"))

    with pytest.raises(JobNotFoundError):
        repository.release_job(job.id, "agent-a")


def test_concurrent_claims_are_mutually_exclusive(tmp_path) -> None:
    """Two agents claiming concurrently must never receive the same task."""
    repository = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    job = repository.create_job(Job(type="demo.echo"))

    results: list[Job | None] = []
    barrier = threading.Barrier(2)

    def claim(agent_id: str) -> None:
        barrier.wait()
        results.append(repository.claim_next_job(agent_id))

    threads = [
        threading.Thread(target=claim, args=("agent-a",)),
        threading.Thread(target=claim, args=("agent-b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    claimed = [r for r in results if r is not None]
    assert len(claimed) == 1
    assert claimed[0].id == job.id


def test_job_with_unmet_dependency_is_not_claimed(tmp_path) -> None:
    repository = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    dep = repository.create_job(Job(type="build"))
    dependent = repository.create_job(Job(type="deploy", depends_on=[dep.id]))

    # Only the dependency itself is claimable; the dependent is blocked.
    first = repository.claim_next_job("agent-a")
    assert first is not None
    assert first.id == dep.id

    # No other claimable job while the dependency is still running.
    assert repository.claim_next_job("agent-b") is None
    # The dependent task retains its declared dependencies.
    assert repository.require_job(dependent.id).depends_on == [dep.id]


def test_dependent_job_claimable_after_dependency_succeeds(tmp_path) -> None:
    repository = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    dep = repository.create_job(Job(type="build"))
    dependent = repository.create_job(Job(type="deploy", depends_on=[dep.id]))

    claimed_dep = repository.claim_next_job("agent-a")
    assert claimed_dep is not None
    repository.mark_succeeded(claimed_dep.id)

    # Now the dependent becomes claimable.
    claimed_dependent = repository.claim_next_job("agent-b")
    assert claimed_dependent is not None
    assert claimed_dependent.id == dependent.id


def test_job_with_multiple_dependencies_waits_for_all(tmp_path) -> None:
    repository = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    dep_a = repository.create_job(Job(type="build-a"))
    dep_b = repository.create_job(Job(type="build-b"))
    dependent = repository.create_job(Job(type="deploy", depends_on=[dep_a.id, dep_b.id]))

    # Complete only the first dependency.
    repository.mark_succeeded(repository.claim_next_job("agent-a").id)
    # Second dependency still pending → dependent blocked, only dep_b claimable.
    claimed = repository.claim_next_job("agent-b")
    assert claimed is not None
    assert claimed.id == dep_b.id
    repository.mark_succeeded(claimed.id)

    # Both dependencies satisfied → dependent now claimable.
    final = repository.claim_next_job("agent-c")
    assert final is not None
    assert final.id == dependent.id


def test_resource_key_blocks_concurrent_claim_of_same_resource(tmp_path) -> None:
    repository = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    first = repository.create_job(Job(type="edit", resource_key="src/foo.py"))
    repository.create_job(Job(type="edit", resource_key="src/foo.py"))

    claimed = repository.claim_next_job("agent-a")
    assert claimed is not None
    assert claimed.id == first.id

    # Second task on the same resource is locked while the first is running.
    assert repository.claim_next_job("agent-b") is None


def test_resource_key_released_after_holder_completes(tmp_path) -> None:
    repository = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    repository.create_job(Job(type="edit", resource_key="src/foo.py"))
    second = repository.create_job(Job(type="edit", resource_key="src/foo.py"))

    claimed_first = repository.claim_next_job("agent-a")
    assert claimed_first is not None
    repository.mark_succeeded(claimed_first.id)

    # Resource lock released → second task now claimable.
    claimed_second = repository.claim_next_job("agent-b")
    assert claimed_second is not None
    assert claimed_second.id == second.id


def test_different_resource_keys_claim_concurrently(tmp_path) -> None:
    repository = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    repository.create_job(Job(type="edit", resource_key="src/foo.py"))
    repository.create_job(Job(type="edit", resource_key="src/bar.py"))

    first = repository.claim_next_job("agent-a")
    second = repository.claim_next_job("agent-b")

    assert first is not None
    assert second is not None
    assert {first.resource_key, second.resource_key} == {"src/foo.py", "src/bar.py"}


def test_register_agent_creates_online_agent(tmp_path) -> None:
    repository = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    agent = repository.register_agent(
        "agent-1", capabilities=["build", "deploy"], metadata={"host": "box-1"}
    )

    assert agent.id == "agent-1"
    assert agent.status == AgentStatus.ONLINE
    assert agent.capabilities == ["build", "deploy"]
    assert agent.metadata == {"host": "box-1"}
    assert repository.get_agent("agent-1").status == AgentStatus.ONLINE


def test_register_agent_is_idempotent_upsert(tmp_path) -> None:
    repository = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    repository.register_agent("agent-1", capabilities=["build"])
    updated = repository.register_agent("agent-1", capabilities=["deploy"])

    assert updated.capabilities == ["deploy"]
    assert len(repository.list_agents()) == 1


def test_agent_heartbeat_updates_liveness(tmp_path) -> None:
    repository = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    agent = repository.register_agent("agent-1")
    before = agent.last_heartbeat_at

    refreshed = repository.agent_heartbeat("agent-1")
    assert refreshed.status == AgentStatus.ONLINE
    assert refreshed.last_heartbeat_at >= before


def test_agent_heartbeat_unknown_agent_raises(tmp_path) -> None:
    repository = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    with pytest.raises(AgentNotFoundError):
        repository.agent_heartbeat("ghost")


def test_mark_stale_agents_offline_reaps_only_stale(tmp_path) -> None:
    repository = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    repository.register_agent("agent-1")

    # A generous timeout evaluated at the present time leaves a fresh agent online.
    assert repository.mark_stale_agents_offline(timeout=timedelta(hours=1)) == []
    assert repository.get_agent("agent-1").status == AgentStatus.ONLINE

    # Evaluated far in the future with a tiny timeout, the agent is now stale.
    future = datetime.now(UTC) + timedelta(hours=1)
    reaped = repository.mark_stale_agents_offline(timeout=timedelta(seconds=1), now=future)
    assert {a.id for a in reaped} == {"agent-1"}
    assert repository.get_agent("agent-1").status == AgentStatus.OFFLINE


def test_reaped_agent_comes_back_online_on_heartbeat(tmp_path) -> None:
    repository = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    repository.register_agent("agent-1")
    future = datetime.now(UTC) + timedelta(hours=1)
    repository.mark_stale_agents_offline(timeout=timedelta(seconds=1), now=future)
    assert repository.get_agent("agent-1").status == AgentStatus.OFFLINE

    repository.agent_heartbeat("agent-1")
    assert repository.get_agent("agent-1").status == AgentStatus.ONLINE


def test_list_agents_filters_by_status(tmp_path) -> None:
    repository = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    repository.register_agent("online-1")
    repository.register_agent("offline-1")
    future = datetime.now(UTC) + timedelta(hours=1)
    # Reap everyone, then bring one back.
    repository.mark_stale_agents_offline(timeout=timedelta(seconds=1), now=future)
    repository.agent_heartbeat("online-1")

    online = repository.list_agents(status=AgentStatus.ONLINE.value)
    offline = repository.list_agents(status=AgentStatus.OFFLINE.value)
    assert {a.id for a in online} == {"online-1"}
    assert {a.id for a in offline} == {"offline-1"}
