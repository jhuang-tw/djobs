"""Tests for job inspection and observability features."""

from __future__ import annotations

from djobs.queue.service import QueueService
from djobs.storage.sqlite import SQLiteJobRepository

# ------------------------------------------------------------------
# inspect_job
# ------------------------------------------------------------------


def test_inspect_job_basic(tmp_path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)

    submitted = queue.submit("demo.echo", {"msg": "hi"})
    result = queue.inspect(submitted.id)

    assert result["job_id"] == submitted.id
    assert result["type"] == "demo.echo"
    assert result["status"] == "pending"
    assert result["correlation_id"] == submitted.correlation_id
    assert result["attempt"] == 0
    assert result["event_count"] == 1
    assert result["events"][0]["event"] == "job_created"


def test_inspect_after_claim_shows_started_at(tmp_path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)

    submitted = queue.submit("demo.echo")
    queue.claim("w-1")

    result = queue.inspect(submitted.id)
    assert result["status"] == "running"
    assert result["started_at"] is not None
    assert result["duration_seconds"] is not None
    assert result["duration_seconds"] >= 0


def test_inspect_after_failure_shows_error(tmp_path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)

    submitted = queue.submit("demo.echo")
    queue.claim("w-1")
    queue.fail(submitted.id, "connection timeout")

    result = queue.inspect(submitted.id)
    assert result["status"] == "failed"
    assert result["last_error"] == "connection timeout"
    assert result["event_count"] == 3  # created, claimed, failed


def test_inspect_nonexistent_raises(tmp_path) -> None:
    import pytest

    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)

    with pytest.raises(Exception, match="not found"):
        queue.inspect("nonexistent-id")


# ------------------------------------------------------------------
# correlation_id
# ------------------------------------------------------------------


def test_correlation_id_auto_generated(tmp_path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)

    j1 = queue.submit("a")
    j2 = queue.submit("b")

    assert j1.correlation_id is not None
    assert j2.correlation_id is not None
    assert j1.correlation_id != j2.correlation_id


def test_correlation_id_user_supplied(tmp_path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)

    submitted = queue.submit("a", correlation_id="my-trace-123")
    assert submitted.correlation_id == "my-trace-123"

    refreshed = queue.get_job(submitted.id)
    assert refreshed is not None
    assert refreshed.correlation_id == "my-trace-123"


def test_correlation_id_in_created_event(tmp_path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)

    submitted = queue.submit("a", correlation_id="trace-456")
    events = queue.events(submitted.id)
    created_event = events[0]
    assert created_event.metadata.get("correlation_id") == "trace-456"


# ------------------------------------------------------------------
# health
# ------------------------------------------------------------------


def test_health_returns_queue_depth(tmp_path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)

    queue.submit("a")
    queue.submit("b")
    queue.claim("w-1")

    h = queue.health()
    assert h["status"] == "ok"
    assert h["queue_depth"]["pending"] == 1
    assert h["queue_depth"]["running"] == 1
    assert h["total_jobs"] == 2


def test_health_empty_queue(tmp_path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)

    h = queue.health()
    assert h["status"] == "ok"
    assert h["total_jobs"] == 0


# ------------------------------------------------------------------
# started_at tracking
# ------------------------------------------------------------------


def test_started_at_set_on_claim(tmp_path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)

    queue.submit("demo.echo")
    claimed = queue.claim("w-1")
    assert claimed is not None
    assert claimed.started_at is not None


def test_started_at_none_before_claim(tmp_path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)

    submitted = queue.submit("demo.echo")
    assert submitted.started_at is None
