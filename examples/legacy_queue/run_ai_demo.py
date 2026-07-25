"""Legacy durable-queue demo: the former Phase 8 AI Task Platform.

This demonstrates the original queue, worker-pool, and scheduler subsystem. It is not the
recommended onboarding path for djobs local repository memory.
"""

from __future__ import annotations

import os
import random
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from djobs.api.ai_handlers import AI_HANDLERS
from djobs.core.retry import RetryPolicy
from djobs.queue.service import QueueService
from djobs.scheduler.scheduler import SchedulerLoop
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.worker.pool import WorkerPool
from djobs.worker.registry import HandlerRegistry


def main() -> None:
    random.seed(42)

    default_db = str(Path(__file__).with_name("phase8_ai_demo.db"))
    db_path = Path(os.getenv("DJOBS_AI_DEMO_DB_PATH", default_db))
    repo = SQLiteJobRepository.from_path(db_path)
    queue = QueueService(repo, retry_policy=RetryPolicy(base_delay_seconds=0.1))
    registry = HandlerRegistry()

    cost_tracker: dict[str, dict] = {}
    cost_lock = threading.Lock()

    def _wrap_handler(job_type, original):
        def wrapper(payload):
            result = original(payload)
            with cost_lock:
                cost_tracker[job_type] = cost_tracker.get(job_type, {})
                cost_tracker[job_type]["tokens"] = (
                    cost_tracker[job_type].get("tokens", 0) + payload.get("tokens_used", 0)
                )
                cost_tracker[job_type]["cost"] = (
                    cost_tracker[job_type].get("cost", 0.0) + payload.get("cost_usd", 0.0)
                )
            return result

        return wrapper

    for job_type, handler in AI_HANDLERS.items():
        registry.register(job_type, _wrap_handler(job_type, handler))

    batch_id = f"batch-{datetime.now(timezone.utc).strftime('%H%M%S')}"
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
        type_concurrency_limits={"ai.generate": 1},
    )

    pool_thread = threading.Thread(
        target=pool.run_loop,
        args=(stop,),
        kwargs={"poll_interval": 0.05},
    )
    pool_thread.start()

    print("[2] Processing...")
    for _ in range(200):
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

    print("\n[3] Results:")
    for job in submitted:
        info = queue.inspect(job.id)
        print(
            f"  {info['type']:15s} "
            f"status={info['status']:17s} "
            f"attempts={info['attempt']} "
            f"events={info['event_count']}"
        )

    total_tokens = sum(value.get("tokens", 0) for value in cost_tracker.values())
    total_cost = sum(value.get("cost", 0.0) for value in cost_tracker.values())

    print("\n[4] Summary:")
    print(f"    Queue health: {queue.health()}")
    print(f"    Total tokens: {total_tokens}")
    print(f"    Total cost:   ${total_cost:.6f}")
    print(f"    Pool stats:   completed={pool.completed_count}, failed={pool.failed_count}")
    print(f"    Correlation:  {batch_id}")


if __name__ == "__main__":
    main()
