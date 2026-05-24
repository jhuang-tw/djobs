"""Phase 8 final demo: AI Task Platform.

Demonstrates the full system capability:
1. Batch submission of mixed AI job types.
2. WorkerPool with concurrency control + per-type limits.
3. Scheduler loop for retry promotion + crash recovery.
4. Job inspection with cost/token tracking.
5. Health check and backlog metrics.
6. Correlation ID linking all jobs in the batch.
"""

from __future__ import annotations

import os
import random
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from djobs.api.ai_handlers import AI_HANDLERS
from djobs.core.retry import RetryPolicy
from djobs.queue.service import QueueService
from djobs.scheduler.scheduler import SchedulerLoop
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.worker.pool import WorkerPool
from djobs.worker.registry import HandlerRegistry


def main() -> None:
    random.seed(42)  # reproducible demo

    default_db = str(Path(__file__).with_name("phase8_ai_demo.db"))
    db_path = Path(os.getenv("DJOBS_AI_DEMO_DB_PATH", default_db))
    repo = SQLiteJobRepository.from_path(db_path)
    queue = QueueService(repo, retry_policy=RetryPolicy(base_delay_seconds=0.1))
    registry = HandlerRegistry()

    # In-memory cost tracker (handlers mutate payload in memory, not DB)
    cost_tracker: dict[str, dict] = {}
    cost_lock = threading.Lock()

    def _wrap_handler(job_type, original):
        def wrapper(payload):
            result = original(payload)
            with cost_lock:
                cost_tracker[job_type] = cost_tracker.get(job_type, {})
                cost_tracker[job_type]["tokens"] = (
                    cost_tracker[job_type].get("tokens", 0)
                    + payload.get("tokens_used", 0)
                )
                cost_tracker[job_type]["cost"] = (
                    cost_tracker[job_type].get("cost", 0.0)
                    + payload.get("cost_usd", 0.0)
                )
            return result
        return wrapper

    for job_type, handler in AI_HANDLERS.items():
        registry.register(job_type, _wrap_handler(job_type, handler))

    # ------------------------------------------------------------------
    # 1. Batch submit
    # ------------------------------------------------------------------
    batch_id = f"batch-{datetime.now(UTC).strftime('%H%M%S')}"
    jobs_spec = [
        {
            "type": "ai.summarize",
            "payload": {"text": "The quick brown fox jumps. " * 5},
            "max_attempts": 3,
        },
        {
            "type": "ai.summarize",
            "payload": {"text": "Distributed systems are fascinating. " * 3},
            "max_attempts": 3,
        },
        {
            "type": "ai.classify",
            "payload": {"text": "I love this product!", "labels": ["positive", "negative"]},
            "max_attempts": 2,
        },
        {
            "type": "ai.classify",
            "payload": {"text": "Terrible experience.", "labels": ["positive", "negative"]},
            "max_attempts": 2,
        },
        {
            "type": "ai.generate",
            "payload": {
                "prompt": "Explain retry storms in distributed systems",
                "max_tokens": 200,
            },
            "max_attempts": 4,
        },
        {
            "type": "ai.generate",
            "payload": {"prompt": "Write a haiku about job queues", "max_tokens": 100},
            "max_attempts": 4,
        },
    ]

    submitted = queue.submit_batch(jobs_spec, correlation_id=batch_id)
    print(f"[1] Submitted {len(submitted)} AI jobs (correlation_id={batch_id})")
    print(f"    Backlog: {queue.backlog()}")

    # ------------------------------------------------------------------
    # 2. Start scheduler + worker pool
    # ------------------------------------------------------------------
    stop = threading.Event()
    scheduler = SchedulerLoop(queue)

    scheduler_thread = threading.Thread(
        target=scheduler.run_loop,
        kwargs={"interval_seconds": 0.2, "stop_event": stop},
        daemon=True,
    )
    scheduler_thread.start()

    pool = WorkerPool(
        queue,
        registry,
        worker_id="ai-worker-1",
        max_concurrent=3,
        type_concurrency_limits={"ai.generate": 1},  # expensive → limit concurrency
    )

    pool_thread = threading.Thread(
        target=pool.run_loop,
        args=(stop,),
        kwargs={"poll_interval": 0.05},
    )
    pool_thread.start()

    # ------------------------------------------------------------------
    # 3. Wait for completion
    # ------------------------------------------------------------------
    print("[2] Processing...")
    for _ in range(200):  # max ~10s
        time.sleep(0.05)
        backlog = queue.backlog()
        pending = (
            backlog.get("pending", 0)
            + backlog.get("running", 0)
            + backlog.get("retry_scheduled", 0)
        )
        if pending == 0:
            break

    stop.set()
    pool_thread.join(timeout=5)
    scheduler_thread.join(timeout=5)

    # ------------------------------------------------------------------
    # 4. Results
    # ------------------------------------------------------------------
    print("\n[3] Results:")
    for job in submitted:
        info = queue.inspect(job.id)
        print(
            f"  {info['type']:15s} "
            f"status={info['status']:17s} "
            f"attempts={info['attempt']} "
            f"events={info['event_count']}"
        )

    # ------------------------------------------------------------------
    # 5. Summary
    # ------------------------------------------------------------------
    total_tokens = sum(v.get("tokens", 0) for v in cost_tracker.values())
    total_cost = sum(v.get("cost", 0.0) for v in cost_tracker.values())

    print("\n[4] Summary:")
    health = queue.health()
    print(f"    Queue health: {health}")
    print(f"    Total tokens: {total_tokens}")
    print(f"    Total cost:   ${total_cost:.6f}")
    print(f"    Pool stats:   completed={pool.completed_count}, failed={pool.failed_count}")
    print(f"    Correlation:  {batch_id}")


if __name__ == "__main__":
    main()
