"""Run a Phase 2 retry job end-to-end with SQLite."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from djobs.core.errors import RetryableJobError
from djobs.queue.service import QueueService
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.worker.registry import HandlerRegistry
from djobs.worker.runner import WorkerRunner


def main() -> None:
    default_db_path = str(Path(__file__).with_name("phase2_retry_demo.db"))
    db_path = Path(os.getenv("DJOBS_RETRY_EXAMPLE_DB_PATH", default_db_path))
    repository = SQLiteJobRepository.from_path(db_path)
    queue = QueueService(repository)
    registry = HandlerRegistry()
    attempts: list[dict[str, Any]] = []

    def flaky_handler(payload: dict[str, Any]) -> None:
        attempts.append(payload)
        if len(attempts) == 1:
            raise RetryableJobError("temporary downstream outage")
        print(f"processed after retry: {payload}")

    registry.register("demo.flaky", flaky_handler)
    submitted_job = queue.submit(
        "demo.flaky",
        {"message": "hello from Phase 2"},
        max_attempts=2,
        idempotency_key="phase2-demo-job",
    )
    runner = WorkerRunner(queue, registry, worker_id="retry-demo-worker")

    first_result = runner.run_once()
    retry_job = queue.get_job(submitted_job.id)
    if retry_job is None or retry_job.run_after is None:
        raise RuntimeError("expected job to be scheduled for retry")

    promoted_jobs = queue.promote_due_retries(now=retry_job.run_after)
    second_result = runner.run_once()
    final_job = queue.get_job(submitted_job.id)

    print(f"first_status: {first_result.job.status.value if first_result.job else 'missing'}")
    print(f"promoted: {[job.id for job in promoted_jobs]}")
    print(f"second_status: {second_result.job.status.value if second_result.job else 'missing'}")
    print(f"final_status: {final_job.status.value if final_job else 'missing'}")
    print(f"attempts: {final_job.attempt if final_job else 'missing'}")
    print("events:")
    for event in queue.events(submitted_job.id):
        print(f"- {event.event_type}")


if __name__ == "__main__":
    main()
