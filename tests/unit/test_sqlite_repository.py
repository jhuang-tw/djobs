"""Tests for SQLiteJobRepository."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from djobs.core.models import Job
from djobs.core.states import JobStatus
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
