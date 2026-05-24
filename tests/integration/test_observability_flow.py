"""Integration test: observability through full job lifecycle."""

from __future__ import annotations

from typing import Any

from djobs.core.errors import RetryableJobError
from djobs.queue.service import QueueService
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.worker.registry import HandlerRegistry
from djobs.worker.runner import WorkerRunner


def test_inspect_full_lifecycle(tmp_path) -> None:
    """Submit → claim → succeed, then inspect shows full timeline + duration."""
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)
    registry = HandlerRegistry()
    registry.register("echo", lambda p: None)

    submitted = queue.submit("echo", {"v": 1}, correlation_id="trace-abc")
    runner = WorkerRunner(queue, registry, worker_id="w-1")
    runner.run_once()

    result = queue.inspect(submitted.id)
    assert result["status"] == "succeeded"
    assert result["correlation_id"] == "trace-abc"
    assert result["duration_seconds"] is not None
    assert result["duration_seconds"] >= 0
    assert result["event_count"] == 3
    events = [e["event"] for e in result["events"]]
    assert events == ["job_created", "job_claimed", "job_succeeded"]


def test_inspect_retry_lifecycle(tmp_path) -> None:
    """Submit → fail (retryable) → inspect shows retry_scheduled + error."""
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)
    registry = HandlerRegistry()

    def flaky(payload: dict[str, Any]) -> None:
        raise RetryableJobError("transient")

    registry.register("flaky", flaky)

    submitted = queue.submit("flaky", max_attempts=3)
    runner = WorkerRunner(queue, registry, worker_id="w-1")
    runner.run_once()

    result = queue.inspect(submitted.id)
    assert result["status"] == "retry_scheduled"
    assert result["last_error"] == "transient"
    assert result["attempt"] == 1
    assert result["max_attempts"] == 3

    events = [e["event"] for e in result["events"]]
    assert events == ["job_created", "job_claimed", "retry_scheduled"]


def test_health_after_mixed_workload(tmp_path) -> None:
    """Health endpoint reflects mixed job states."""
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)
    registry = HandlerRegistry()
    registry.register("ok", lambda p: None)
    registry.register("bad", lambda p: (_ for _ in ()).throw(RuntimeError("boom")))

    queue.submit("ok")
    queue.submit("bad")
    queue.submit("ok")  # stays pending

    runner = WorkerRunner(queue, registry, worker_id="w-1")
    runner.run_once()  # ok → succeeded
    runner.run_once()  # bad → failed

    h = queue.health()
    assert h["status"] == "ok"
    assert h["queue_depth"].get("succeeded", 0) == 1
    assert h["queue_depth"].get("failed", 0) == 1
    assert h["queue_depth"].get("pending", 0) == 1
    assert h["total_jobs"] == 3
