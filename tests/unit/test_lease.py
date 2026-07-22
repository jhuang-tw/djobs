"""Tests for Phase 3 lease, heartbeat, and crash recovery."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from djobs.core.errors import JobNotFoundError
from djobs.core.models import Job
from djobs.core.states import JobStatus
from djobs.storage.sqlite import SQLiteJobRepository


def test_claim_sets_lease_fields(tmp_path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    repo.create_job(Job(type="demo.echo"))

    claimed = repo.claim_next_job("worker-1")

    assert claimed is not None
    assert claimed.leased_by == "worker-1"
    assert claimed.lease_expires_at is not None
    assert claimed.heartbeat_at is not None
    assert claimed.status == JobStatus.RUNNING


def test_claim_with_custom_lease_duration(tmp_path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    repo.create_job(Job(type="demo.echo"))

    before = datetime.now(timezone.utc)
    claimed = repo.claim_next_job("worker-1", lease_duration=timedelta(minutes=5))

    assert claimed is not None
    assert claimed.lease_expires_at is not None
    assert claimed.lease_expires_at >= before + timedelta(minutes=4, seconds=59)


def test_succeeded_clears_lease(tmp_path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    repo.create_job(Job(type="demo.echo"))
    claimed = repo.claim_next_job("worker-1")

    assert claimed is not None
    succeeded = repo.mark_succeeded(claimed.id)

    assert succeeded.leased_by is None
    assert succeeded.lease_expires_at is None
    assert succeeded.heartbeat_at is None


def test_failed_clears_lease(tmp_path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    repo.create_job(Job(type="demo.echo"))
    claimed = repo.claim_next_job("worker-1")

    assert claimed is not None
    failed = repo.mark_failed(claimed.id, "boom")

    assert failed.leased_by is None
    assert failed.lease_expires_at is None


def test_heartbeat_extends_lease(tmp_path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    repo.create_job(Job(type="demo.echo"))
    claimed = repo.claim_next_job("worker-1", lease_duration=timedelta(seconds=5))

    assert claimed is not None
    original_expires = claimed.lease_expires_at

    updated = repo.heartbeat(claimed.id, "worker-1", lease_duration=timedelta(seconds=30))

    assert updated.lease_expires_at is not None
    assert updated.lease_expires_at > original_expires
    assert updated.heartbeat_at is not None


def test_heartbeat_rejects_wrong_worker(tmp_path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    repo.create_job(Job(type="demo.echo"))
    claimed = repo.claim_next_job("worker-1")

    assert claimed is not None
    with pytest.raises(JobNotFoundError, match="not leased by"):
        repo.heartbeat(claimed.id, "worker-2")


def test_heartbeat_rejects_non_running_job(tmp_path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    job = repo.create_job(Job(type="demo.echo"))

    with pytest.raises(JobNotFoundError, match="Cannot heartbeat"):
        repo.heartbeat(job.id, "worker-1")


def test_recover_expired_leases_moves_to_pending(tmp_path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    repo.create_job(Job(type="demo.echo", max_attempts=3))
    claimed = repo.claim_next_job("worker-1", lease_duration=timedelta(seconds=1))

    assert claimed is not None
    future = datetime.now(timezone.utc) + timedelta(seconds=10)
    recovered = repo.recover_expired_leases(now=future)

    assert len(recovered) == 1
    assert recovered[0].status == JobStatus.PENDING
    assert recovered[0].leased_by is None
    assert recovered[0].lease_expires_at is None
    events = repo.list_events(claimed.id)
    assert events[-1].event_type == "lease_expired"


def test_recover_does_not_touch_active_lease(tmp_path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    repo.create_job(Job(type="demo.echo", max_attempts=2))
    claimed = repo.claim_next_job("worker-1", lease_duration=timedelta(minutes=10))

    assert claimed is not None
    recovered = repo.recover_expired_leases(now=datetime.now(timezone.utc))

    assert recovered == []
    assert repo.require_job(claimed.id).status == JobStatus.RUNNING


def test_recover_expired_lease_respects_max_attempts(tmp_path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    repo.create_job(Job(type="demo.echo", max_attempts=1))
    claimed = repo.claim_next_job("worker-1", lease_duration=timedelta(seconds=1))

    assert claimed is not None
    future = datetime.now(timezone.utc) + timedelta(seconds=10)
    recovered = repo.recover_expired_leases(now=future)

    assert len(recovered) == 1
    assert recovered[0].status == JobStatus.RETRY_SCHEDULED
