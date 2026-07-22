"""Run Phase 4 scheduler loop demo: crash recovery + retry promotion.

Demonstrates:
1. Worker crashes (lease expires) → SchedulerLoop.tick() recovers the job.
2. Second worker picks up and fails with retryable error → retry_scheduled.
3. Scheduler tick promotes the retry → pending.
4. Second worker retries → succeeds.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from djobs.core.errors import RetryableJobError
from djobs.queue.service import QueueService
from djobs.scheduler.scheduler import SchedulerLoop
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.worker.registry import HandlerRegistry
from djobs.worker.runner import WorkerRunner


def main() -> None:
    default_db_path = str(Path(__file__).with_name("phase4_scheduler_demo.db"))
    db_path = Path(os.getenv("DJOBS_SCHEDULER_EXAMPLE_DB_PATH", default_db_path))
    repository = SQLiteJobRepository.from_path(db_path)
    queue = QueueService(repository)
    scheduler = SchedulerLoop(queue)
    registry = HandlerRegistry()

    attempt_counter = {"n": 0}

    def flaky_handler(payload: dict[str, Any]) -> None:
        attempt_counter["n"] += 1
        if attempt_counter["n"] == 1:
            raise RetryableJobError("transient network error")
        print(f"  handler succeeded: {payload}")

    registry.register("demo.flaky", flaky_handler)

    # Submit a job with max_attempts=3
    submitted = queue.submit(
        "demo.flaky",
        {"message": "Phase 4 scheduler demo"},
        max_attempts=3,
    )
    print(f"[1] Submitted job {submitted.id}")

    # Worker-A claims the job, then "crashes" (short lease, no heartbeat)
    repository.claim_next_job("worker-A", lease_duration=timedelta(seconds=1))
    print("[2] worker-A claimed job (simulating crash, lease=1s)")

    # Scheduler tick at far-future recovers the expired lease
    far = datetime.now(timezone.utc) + timedelta(seconds=10)
    tick1 = scheduler.tick(now=far)
    print(f"[3] Scheduler tick: recovered={tick1.recovered}")

    # Worker-B picks up — first real handler attempt fails (RetryableJobError)
    runner = WorkerRunner(queue, registry, worker_id="worker-B")
    result1 = runner.run_once()
    status1 = result1.job.status.value if result1.job else "?"
    print(f"[4] worker-B attempt: status={status1}, error={result1.error}")

    # Scheduler tick promotes the retry_scheduled → pending
    retry_job = queue.get_job(submitted.id)
    if retry_job and retry_job.run_after:
        tick2 = scheduler.tick(now=retry_job.run_after)
    else:
        tick2 = scheduler.tick(now=datetime.now(timezone.utc) + timedelta(hours=1))
    print(f"[5] Scheduler tick: promoted={tick2.promoted}")

    # Worker-B retries — succeeds
    result2 = runner.run_once()
    print(f"[6] worker-B retry: status={result2.job.status.value if result2.job else '?'}")

    final = queue.get_job(submitted.id)
    print(f"\nFinal status: {final.status.value if final else 'missing'}")
    print(f"Total handler attempts: {attempt_counter['n']}")
    print("\nEvents:")
    for event in queue.events(submitted.id):
        print(f"  - {event.event_type}")


if __name__ == "__main__":
    main()
