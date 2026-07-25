"""Legacy durable-queue demo: crash recovery through historical MCP task tools.

This demonstrates the original durable task subsystem. For the current local-memory product,
start with ``examples/memory_walkthrough.py``.
"""

from __future__ import annotations

import json
import random
import threading
import time
from pathlib import Path

from djobs.api.ai_handlers import AI_HANDLERS
from djobs.core.retry import RetryPolicy
from djobs.mcp_server import (
    check_task,
    configure,
    enqueue_task,
    health,
    resume_session,
)
from djobs.queue.service import QueueService
from djobs.scheduler.scheduler import SchedulerLoop
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.worker.pool import WorkerPool
from djobs.worker.registry import HandlerRegistry

CORRELATION_ID = "demo-workspace:/src/my/project"


def _make_pool(
    queue: QueueService, registry: HandlerRegistry
) -> tuple[WorkerPool, threading.Event, threading.Thread, threading.Thread]:
    stop = threading.Event()
    scheduler = SchedulerLoop(queue)
    scheduler_thread = threading.Thread(
        target=scheduler.run_loop,
        kwargs={"interval_seconds": 0.2, "stop_event": stop},
        daemon=True,
    )
    pool = WorkerPool(
        queue,
        registry,
        worker_id="durable-worker",
        max_concurrent=3,
        type_concurrency_limits={"ai.generate": 1},
    )
    pool_thread = threading.Thread(
        target=pool.run_loop,
        args=(stop,),
        kwargs={"poll_interval": 0.05},
    )
    return pool, stop, scheduler_thread, pool_thread


def main() -> None:
    random.seed(42)
    db_path = str(Path(__file__).with_name("phase9_durable_demo.db"))
    database = Path(db_path)
    if database.exists():
        database.unlink()

    configure(db_path)

    repo = SQLiteJobRepository.from_path(db_path)
    direct_queue = QueueService(repo, retry_policy=RetryPolicy(base_delay_seconds=0.1))
    registry = HandlerRegistry()
    for job_type, handler in AI_HANDLERS.items():
        registry.register(job_type, handler)

    print("=" * 60)
    print("STEP 1: Resume check (first run)")
    print("=" * 60)
    result = json.loads(resume_session(CORRELATION_ID))
    print(f"  → {result['message']}")
    print()

    print("=" * 60)
    print("STEP 2: Enqueue 8 durable tasks")
    print("=" * 60)

    tasks = [
        ("ai.summarize", {"text": f"Document {index}: important content. " * 5}, 3)
        for index in range(4)
    ] + [
        ("ai.classify", {"text": "Great product!", "labels": ["pos", "neg"]}, 2),
        ("ai.classify", {"text": "Terrible.", "labels": ["pos", "neg"]}, 2),
        ("ai.generate", {"prompt": "Explain retry logic", "max_tokens": 100}, 4),
        ("ai.generate", {"prompt": "Write a haiku about queues", "max_tokens": 50}, 4),
    ]

    task_ids = []
    for task_type, payload, max_attempts in tasks:
        result_json = enqueue_task(
            task_type=task_type,
            payload=json.dumps(payload),
            max_attempts=max_attempts,
            correlation_id=CORRELATION_ID,
        )
        task = json.loads(result_json)
        task_ids.append(task["id"])
        print(f"  Enqueued: {task['type']:15s} id={task['id'][:8]}...")

    print(f"\n  Health: {health()}")
    print()

    print("=" * 60)
    print("STEP 3: Process partially, then simulate CRASH")
    print("=" * 60)

    _pool, stop, scheduler_thread, pool_thread = _make_pool(direct_queue, registry)
    scheduler_thread.start()
    pool_thread.start()
    time.sleep(0.8)
    stop.set()
    pool_thread.join(timeout=2)
    scheduler_thread.join(timeout=2)

    completed = 0
    for task_id in task_ids:
        info = json.loads(check_task(task_id))
        if info["status"] == "succeeded":
            completed += 1
    print(f"  Completed before crash: {completed}/{len(task_ids)}")
    print("  *** SIMULATED CRASH — worker killed ***")
    print()

    print("=" * 60)
    print("STEP 4: New session — resume_session discovers unfinished tasks")
    print("=" * 60)

    configure(db_path)
    repo2 = SQLiteJobRepository.from_path(db_path)
    direct_queue2 = QueueService(repo2, retry_policy=RetryPolicy(base_delay_seconds=0.1))

    recovered = direct_queue2.recover_expired_leases()
    print(f"  Recovered {len(recovered)} expired lease(s)")

    result = json.loads(resume_session(CORRELATION_ID))
    print(f"  → {result['message']}")
    for task in result["tasks"]:
        print(
            f"    {task['type']:15s} status={task['status']:17s} id={task['id'][:8]}..."
        )
    print()

    print("=" * 60)
    print("STEP 5: Resume — process remaining tasks to completion")
    print("=" * 60)

    registry2 = HandlerRegistry()
    for job_type, handler in AI_HANDLERS.items():
        registry2.register(job_type, handler)

    _pool2, stop2, scheduler_thread2, pool_thread2 = _make_pool(direct_queue2, registry2)
    scheduler_thread2.start()
    pool_thread2.start()

    for _ in range(200):
        time.sleep(0.05)
        backlog = direct_queue2.backlog()
        pending = (
            backlog.get("pending", 0)
            + backlog.get("running", 0)
            + backlog.get("retry_scheduled", 0)
        )
        if pending == 0:
            break

    stop2.set()
    pool_thread2.join(timeout=5)
    scheduler_thread2.join(timeout=5)

    print()
    print("=" * 60)
    print("FINAL REPORT")
    print("=" * 60)

    all_succeeded = True
    for task_id in task_ids:
        info = json.loads(check_task(task_id))
        status = info["status"]
        if status != "succeeded":
            all_succeeded = False
        print(
            f"  {info['type']:15s} "
            f"status={status:17s} "
            f"attempts={info['attempt']} "
            f"id={info['job_id'][:8]}..."
        )

    queue_health = json.loads(health())
    print(f"\n  Health: {json.dumps(queue_health)}")
    verdict = (
        "✓ ALL TASKS COMPLETED — zero data loss after crash"
        if all_succeeded
        else "✗ Some tasks still incomplete"
    )
    print(f"\n  {verdict}")


if __name__ == "__main__":
    main()
