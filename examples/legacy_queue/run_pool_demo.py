"""Legacy durable-queue demo: worker pool concurrency and graceful drain.

This example belongs to the original queue engine. For the current local-memory product,
start with ``examples/memory_walkthrough.py``.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

from djobs.queue.service import QueueService
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.worker.pool import WorkerPool
from djobs.worker.registry import HandlerRegistry


def main() -> None:
    default_db = str(Path(__file__).with_name("phase5_pool_demo.db"))
    db_path = Path(os.getenv("DJOBS_POOL_EXAMPLE_DB_PATH", default_db))
    repo = SQLiteJobRepository.from_path(db_path)
    queue = QueueService(repo)
    registry = HandlerRegistry()

    def email_handler(payload: dict[str, Any]) -> None:
        print(f"  [email] sending to {payload.get('to', '?')} ...")
        time.sleep(0.3)
        print(f"  [email] sent to {payload.get('to', '?')}")

    def compute_handler(payload: dict[str, Any]) -> None:
        print(f"  [compute] crunching {payload.get('task', '?')} ...")
        time.sleep(0.1)
        print(f"  [compute] done {payload.get('task', '?')}")

    registry.register("email", email_handler)
    registry.register("compute", compute_handler)

    for i in range(3):
        queue.submit("email", {"to": f"user{i}@example.com"})
    for i in range(3):
        queue.submit("compute", {"task": f"batch-{i}"})

    print(f"[1] Backlog after submit: {queue.backlog()}")

    pool = WorkerPool(
        queue,
        registry,
        worker_id="pool-demo",
        max_concurrent=2,
        type_concurrency_limits={"email": 1},
    )

    stop = threading.Event()

    def _monitor() -> None:
        while not stop.is_set():
            time.sleep(0.2)
            backlog = queue.backlog()
            pending = backlog.get("pending", 0)
            running = backlog.get("running", 0)
            if pending == 0 and running == 0:
                time.sleep(0.3)
                stop.set()

    threading.Thread(target=_monitor, daemon=True).start()

    print("[2] Starting WorkerPool (max_concurrent=2, email limit=1)")
    pool.run_loop(stop, poll_interval=0.05)

    print(f"\n[3] Final backlog: {queue.backlog()}")
    print(f"    Completed: {pool.completed_count}")
    print(f"    Failed:    {pool.failed_count}")


if __name__ == "__main__":
    main()
