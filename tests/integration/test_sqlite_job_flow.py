"""Integration tests for Phase 1 SQLite-backed job flow."""

from __future__ import annotations

from typing import Any

from djobs.core.states import JobStatus
from djobs.queue.service import QueueService
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.worker.registry import HandlerRegistry
from djobs.worker.runner import WorkerRunner


def test_sqlite_echo_job_flow(tmp_path) -> None:
    repository = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repository)
    registry = HandlerRegistry()
    seen_payloads: list[dict[str, Any]] = []

    def echo_handler(payload: dict[str, Any]) -> dict[str, Any]:
        seen_payloads.append(payload)
        return payload

    registry.register("demo.echo", echo_handler)
    submitted_job = queue.submit("demo.echo", {"message": "hello"})
    runner = WorkerRunner(queue, registry, worker_id="worker-1")

    result = runner.run_once()
    final_job = queue.get_job(submitted_job.id)

    assert result.did_run is True
    assert seen_payloads == [{"message": "hello"}]
    assert final_job is not None
    assert final_job.status == JobStatus.SUCCEEDED
    assert [event.event_type for event in queue.events(submitted_job.id)] == [
        "job_created",
        "job_claimed",
        "job_succeeded",
    ]
