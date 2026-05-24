"""Tests for WorkerRunner."""

from __future__ import annotations

from typing import Any

from djobs.core.errors import NonRetryableJobError, RetryableJobError
from djobs.core.states import JobStatus
from djobs.queue.service import QueueService
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.worker.registry import HandlerRegistry
from djobs.worker.runner import WorkerRunner


def make_runner(tmp_path):
    repository = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repository)
    registry = HandlerRegistry()
    runner = WorkerRunner(queue, registry, worker_id="worker-1")
    return queue, registry, runner


def test_run_once_returns_idle_when_no_job(tmp_path) -> None:
    queue, registry, runner = make_runner(tmp_path)

    result = runner.run_once()

    assert result.did_run is False
    assert result.job is None
    assert queue.events() == []
    assert registry.has("demo.echo") is False


def test_run_once_success_marks_job_succeeded(tmp_path) -> None:
    queue, registry, runner = make_runner(tmp_path)
    registry.register("demo.echo", lambda payload: {"echo": payload})
    submitted_job = queue.submit("demo.echo", {"message": "hello"})

    result = runner.run_once()

    final_job = queue.get_job(submitted_job.id)

    assert result.did_run is True
    assert result.error is None
    assert final_job is not None
    assert final_job.status == JobStatus.SUCCEEDED


def test_run_once_handler_exception_marks_failed(tmp_path) -> None:
    queue, registry, runner = make_runner(tmp_path)

    def failing_handler(payload: dict[str, Any]) -> None:
        raise RuntimeError(f"failed payload: {payload['message']}")

    registry.register("demo.fail", failing_handler)
    submitted_job = queue.submit("demo.fail", {"message": "hello"})

    result = runner.run_once()
    final_job = queue.get_job(submitted_job.id)

    assert result.did_run is True
    assert result.error == "failed payload: hello"
    assert final_job is not None
    assert final_job.status == JobStatus.FAILED
    assert final_job.last_error == "failed payload: hello"


def test_run_once_unknown_handler_marks_failed(tmp_path) -> None:
    queue, _registry, runner = make_runner(tmp_path)
    submitted_job = queue.submit("demo.missing")

    result = runner.run_once()
    final_job = queue.get_job(submitted_job.id)

    assert result.did_run is True
    assert result.error == "No handler registered for job type 'demo.missing'"
    assert final_job is not None
    assert final_job.status == JobStatus.FAILED
    assert final_job.last_error == "No handler registered for job type 'demo.missing'"


def test_run_once_retryable_error_schedules_retry(tmp_path) -> None:
    queue, registry, runner = make_runner(tmp_path)

    def retryable_handler(payload: dict[str, Any]) -> None:
        raise RetryableJobError(f"retry later: {payload['message']}")

    registry.register("demo.retry", retryable_handler)
    submitted_job = queue.submit("demo.retry", {"message": "hello"}, max_attempts=2)

    result = runner.run_once()
    final_job = queue.get_job(submitted_job.id)

    assert result.did_run is True
    assert result.error == "retry later: hello"
    assert final_job is not None
    assert final_job.status == JobStatus.RETRY_SCHEDULED
    assert final_job.attempt == 1
    assert final_job.run_after is not None


def test_run_once_retryable_error_dead_letters_when_attempts_exhausted(tmp_path) -> None:
    queue, registry, runner = make_runner(tmp_path)

    def retryable_handler(payload: dict[str, Any]) -> None:
        raise RetryableJobError("retry later")

    registry.register("demo.retry", retryable_handler)
    submitted_job = queue.submit("demo.retry", max_attempts=1)

    result = runner.run_once()
    final_job = queue.get_job(submitted_job.id)

    assert result.did_run is True
    assert final_job is not None
    assert final_job.status == JobStatus.DEAD_LETTERED
    assert final_job.last_error == "retry later"


def test_run_once_non_retryable_error_marks_failed(tmp_path) -> None:
    queue, registry, runner = make_runner(tmp_path)
    registry.register(
        "demo.non_retryable",
        lambda payload: (_ for _ in ()).throw(NonRetryableJobError("do not retry")),
    )
    submitted_job = queue.submit("demo.non_retryable", max_attempts=3)

    result = runner.run_once()
    final_job = queue.get_job(submitted_job.id)

    assert result.did_run is True
    assert final_job is not None
    assert final_job.status == JobStatus.FAILED
    assert final_job.last_error == "do not retry"
