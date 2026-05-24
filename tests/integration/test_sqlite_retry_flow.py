"""Integration tests for Phase 2 retry flow."""

from __future__ import annotations

from typing import Any

from djobs.core.errors import RetryableJobError
from djobs.core.states import JobStatus
from djobs.queue.service import QueueService
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.worker.registry import HandlerRegistry
from djobs.worker.runner import WorkerRunner


def test_retryable_failure_promotes_and_then_succeeds(tmp_path) -> None:
    queue = QueueService(SQLiteJobRepository.from_path(tmp_path / "jobs.db"))
    registry = HandlerRegistry()
    calls: list[dict[str, Any]] = []

    def flaky_handler(payload: dict[str, Any]) -> None:
        calls.append(payload)
        if len(calls) == 1:
            raise RetryableJobError("temporary outage")

    registry.register("demo.flaky", flaky_handler)
    submitted_job = queue.submit("demo.flaky", {"message": "hello"}, max_attempts=2)
    runner = WorkerRunner(queue, registry, worker_id="worker-1")

    first_result = runner.run_once()
    retry_job = queue.get_job(submitted_job.id)

    assert first_result.did_run is True
    assert retry_job is not None
    assert retry_job.status == JobStatus.RETRY_SCHEDULED
    assert retry_job.run_after is not None

    queue.promote_due_retries(now=retry_job.run_after)
    second_result = runner.run_once()
    final_job = queue.get_job(submitted_job.id)

    assert second_result.did_run is True
    assert calls == [{"message": "hello"}, {"message": "hello"}]
    assert final_job is not None
    assert final_job.status == JobStatus.SUCCEEDED
    assert final_job.attempt == 2
    assert [event.event_type for event in queue.events(submitted_job.id)] == [
        "job_created",
        "job_claimed",
        "retry_scheduled",
        "retry_promoted",
        "job_claimed",
        "job_succeeded",
    ]
