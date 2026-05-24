"""Run a Phase 1 echo job end-to-end with SQLite."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from djobs.queue.service import QueueService
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.worker.registry import HandlerRegistry
from djobs.worker.runner import WorkerRunner


def echo_handler(payload: dict[str, Any]) -> dict[str, Any]:
    print(f"echo: {payload}")
    return payload


def main() -> None:
    default_db_path = str(Path(__file__).with_name("phase1_demo.db"))
    db_path = Path(os.getenv("DJOBS_EXAMPLE_DB_PATH", default_db_path))
    repository = SQLiteJobRepository.from_path(db_path)
    queue = QueueService(repository)
    registry = HandlerRegistry()
    registry.register("demo.echo", echo_handler)

    submitted_job = queue.submit("demo.echo", {"message": "hello from Phase 1"})
    runner = WorkerRunner(queue, registry, worker_id="demo-worker")
    result = runner.run_once()
    final_job = queue.get_job(submitted_job.id)

    print(f"ran: {result.did_run}")
    print(f"job_id: {submitted_job.id}")
    print(f"final_status: {final_job.status.value if final_job else 'missing'}")
    print("events:")
    for event in queue.events(submitted_job.id):
        print(f"- {event.event_type}")


if __name__ == "__main__":
    main()
