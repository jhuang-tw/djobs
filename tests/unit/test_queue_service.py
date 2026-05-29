"""Tests for QueueService."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from djobs.core.errors import PayloadTooLargeError
from djobs.core.states import JobStatus
from djobs.queue.service import QueueService
from djobs.storage.sqlite import SQLiteJobRepository


def test_submit_creates_pending_job(tmp_path) -> None:
    queue = QueueService(SQLiteJobRepository.from_path(tmp_path / "jobs.db"))

    job = queue.submit("demo.echo", {"message": "hello"})

    assert job.status == JobStatus.PENDING
    assert job.payload == {"message": "hello"}
    assert queue.get_job(job.id) is not None


def test_claim_complete_flow(tmp_path) -> None:
    queue = QueueService(SQLiteJobRepository.from_path(tmp_path / "jobs.db"))
    submitted_job = queue.submit("demo.echo")

    claimed_job = queue.claim("worker-1")
    assert claimed_job is not None
    assert claimed_job.id == submitted_job.id

    completed_job = queue.complete(claimed_job.id)
    assert completed_job.status == JobStatus.SUCCEEDED
    assert [event.event_type for event in queue.events(submitted_job.id)] == [
        "job_created",
        "job_claimed",
        "job_succeeded",
    ]


def test_claim_fail_flow(tmp_path) -> None:
    queue = QueueService(SQLiteJobRepository.from_path(tmp_path / "jobs.db"))
    submitted_job = queue.submit("demo.echo")

    claimed_job = queue.claim("worker-1")
    assert claimed_job is not None

    failed_job = queue.fail(claimed_job.id, "handler failed")
    assert failed_job.status == JobStatus.FAILED
    assert failed_job.last_error == "handler failed"
    assert queue.events(submitted_job.id)[-1].event_type == "job_failed"


def test_submit_returns_existing_active_job_for_same_idempotency_key(tmp_path) -> None:
    queue = QueueService(SQLiteJobRepository.from_path(tmp_path / "jobs.db"))

    first_job = queue.submit("demo.echo", {"value": 1}, idempotency_key="same-key")
    second_job = queue.submit("demo.echo", {"value": 2}, idempotency_key="same-key")

    assert second_job.id == first_job.id
    assert second_job.payload == {"value": 1}
    assert [event.event_type for event in queue.events(first_job.id)] == ["job_created"]


def test_same_idempotency_key_can_create_new_job_after_terminal_state(tmp_path) -> None:
    queue = QueueService(SQLiteJobRepository.from_path(tmp_path / "jobs.db"))
    first_job = queue.submit("demo.echo", idempotency_key="same-key")
    claimed_job = queue.claim("worker-1")

    assert claimed_job is not None
    queue.complete(claimed_job.id)
    second_job = queue.submit("demo.echo", idempotency_key="same-key")

    assert second_job.id != first_job.id


def test_retry_or_dead_letter_schedules_retry_when_attempts_remain(tmp_path) -> None:
    queue = QueueService(SQLiteJobRepository.from_path(tmp_path / "jobs.db"))
    submitted_job = queue.submit("demo.echo", max_attempts=2)
    claimed_job = queue.claim("worker-1")
    now = datetime(2026, 5, 23, 1, 2, 3, tzinfo=UTC)

    assert claimed_job is not None
    retry_job = queue.retry_or_dead_letter(claimed_job.id, "temporary outage", now=now)

    assert retry_job.status == JobStatus.RETRY_SCHEDULED
    assert retry_job.attempt == 1
    assert retry_job.run_after == now + timedelta(seconds=1)
    assert [event.event_type for event in queue.events(submitted_job.id)] == [
        "job_created",
        "job_claimed",
        "retry_scheduled",
    ]


def test_retry_or_dead_letter_sends_to_dlq_when_attempts_exhausted(tmp_path) -> None:
    queue = QueueService(SQLiteJobRepository.from_path(tmp_path / "jobs.db"))
    submitted_job = queue.submit("demo.echo", max_attempts=1)
    claimed_job = queue.claim("worker-1")

    assert claimed_job is not None
    dead_lettered_job = queue.retry_or_dead_letter(claimed_job.id, "temporary outage")

    assert dead_lettered_job.status == JobStatus.DEAD_LETTERED
    assert [event.event_type for event in queue.events(submitted_job.id)] == [
        "job_created",
        "job_claimed",
        "job_dead_lettered",
    ]


def test_promote_due_retries(tmp_path) -> None:
    queue = QueueService(SQLiteJobRepository.from_path(tmp_path / "jobs.db"))
    submitted_job = queue.submit("demo.echo", max_attempts=2)
    claimed_job = queue.claim("worker-1")
    now = datetime(2026, 5, 23, 1, 2, 3, tzinfo=UTC)

    assert claimed_job is not None
    retry_job = queue.retry_or_dead_letter(claimed_job.id, "temporary outage", now=now)
    promoted_jobs = queue.promote_due_retries(now=retry_job.run_after)

    assert [job.id for job in promoted_jobs] == [submitted_job.id]
    assert queue.get_job(submitted_job.id).status == JobStatus.PENDING


# ------------------------------------------------------------------
# Phase 11: evidence on complete
# ------------------------------------------------------------------


def test_complete_with_evidence_stores_in_event(tmp_path) -> None:
    queue = QueueService(SQLiteJobRepository.from_path(tmp_path / "jobs.db"))
    job = queue.submit("refactor")

    queue.complete(job.id, evidence="updated 5 imports")

    events = queue.events(job.id)
    succeeded_events = [e for e in events if e.event_type == "job_succeeded"]
    assert len(succeeded_events) == 1
    assert succeeded_events[0].message == "updated 5 imports"
    assert succeeded_events[0].metadata["evidence"] == "updated 5 imports"


def test_complete_without_evidence_no_metadata(tmp_path) -> None:
    queue = QueueService(SQLiteJobRepository.from_path(tmp_path / "jobs.db"))
    job = queue.submit("refactor")

    queue.complete(job.id)

    events = queue.events(job.id)
    succeeded_events = [e for e in events if e.event_type == "job_succeeded"]
    assert len(succeeded_events) == 1
    assert succeeded_events[0].message is None
    assert succeeded_events[0].metadata == {}


# ------------------------------------------------------------------
# Payload size limit
# ------------------------------------------------------------------


def test_submit_rejects_oversized_payload(tmp_path) -> None:
    queue = QueueService(
        SQLiteJobRepository.from_path(tmp_path / "jobs.db"),
        max_payload_bytes=1024,
    )

    big = {"blob": "x" * 2048}
    with pytest.raises(PayloadTooLargeError):
        queue.submit("demo.echo", big)


def test_submit_accepts_payload_within_limit(tmp_path) -> None:
    queue = QueueService(
        SQLiteJobRepository.from_path(tmp_path / "jobs.db"),
        max_payload_bytes=1024,
    )

    job = queue.submit("demo.echo", {"message": "ok"})
    assert job.status == JobStatus.PENDING


def test_submit_limit_disabled_when_zero(tmp_path) -> None:
    queue = QueueService(
        SQLiteJobRepository.from_path(tmp_path / "jobs.db"),
        max_payload_bytes=0,
    )

    job = queue.submit("demo.echo", {"blob": "x" * 5000})
    assert job.status == JobStatus.PENDING
