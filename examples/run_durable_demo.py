"""Phase 9 demo: Durable AI Agent — crash recovery via MCP tools.

Demonstrates the core value proposition:
1. Submit a batch of tasks via the MCP tool functions.
2. Process some tasks, then simulate a crash (kill the worker mid-flight).
3. Restart and call resume_session to discover incomplete tasks.
4. Resume processing — all tasks complete with no data loss.

Run:
    python examples/run_durable_demo.py
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
    """Create scheduler + worker pool threads."""
    stop = threading.Event()
    scheduler = SchedulerLoop(queue)
    sched_t = threading.Thread(
        target=scheduler.run_loop,
        kwargs={"interval_seconds": 0.2, "stop_event": stop},
        daemon=True,
    )
    pool = WorkerPool(
        queue, registry, worker_id="durable-worker", max_concurrent=3,
        type_concurrency_limits={"ai.generate": 1},
    )
    pool_t = threading.Thread(
        target=pool.run_loop, args=(stop,), kwargs={"poll_interval": 0.05},
    )
    return pool, stop, sched_t, pool_t


def main() -> None:
    random.seed(42)
    db_path = str(Path(__file__).with_name("phase9_durable_demo.db"))
    # Clean slate
    p = Path(db_path)
    if p.exists():
        p.unlink()

    # Configure MCP server to use our db
    configure(db_path)

    # Also set up direct queue + worker for actual processing
    repo = SQLiteJobRepository.from_path(db_path)
    direct_queue = QueueService(repo, retry_policy=RetryPolicy(base_delay_seconds=0.1))
    registry = HandlerRegistry()
    for job_type, handler in AI_HANDLERS.items():
        registry.register(job_type, handler)

    # ================================================================
    # STEP 1: Check for prior session (should be empty)
    # ================================================================
    print("=" * 60)
    print("STEP 1: Resume check (first run)")
    print("=" * 60)
    result = json.loads(resume_session(CORRELATION_ID))
    print(f"  → {result['message']}")
    print()

    # ================================================================
    # STEP 2: Enqueue a batch of 8 tasks (via MCP tool)
    # ================================================================
    print("=" * 60)
    print("STEP 2: Enqueue 8 durable tasks")
    print("=" * 60)

    tasks = [
        ("ai.summarize", {"text": f"Document {i}: important content. " * 5}, 3)
        for i in range(4)
    ] + [
        ("ai.classify", {"text": "Great product!", "labels": ["pos", "neg"]}, 2),
        ("ai.classify", {"text": "Terrible.", "labels": ["pos", "neg"]}, 2),
        ("ai.generate", {"prompt": "Explain retry logic", "max_tokens": 100}, 4),
        ("ai.generate", {"prompt": "Write a haiku about queues", "max_tokens": 50}, 4),
    ]

    task_ids = []
    for task_type, payload, max_att in tasks:
        result_json = enqueue_task(
            task_type=task_type,
            payload=json.dumps(payload),
            max_attempts=max_att,
            correlation_id=CORRELATION_ID,
        )
        task = json.loads(result_json)
        task_ids.append(task["id"])
        print(f"  Enqueued: {task['type']:15s} id={task['id'][:8]}...")

    print(f"\n  Health: {health()}")
    print()

    # ================================================================
    # STEP 3: Process SOME tasks then CRASH
    # ================================================================
    print("=" * 60)
    print("STEP 3: Process partially, then simulate CRASH")
    print("=" * 60)

    _pool, stop, sched_t, pool_t = _make_pool(direct_queue, registry)
    sched_t.start()
    pool_t.start()

    # Let it run briefly — process ~3-4 jobs
    time.sleep(0.8)

    # CRASH! Kill everything abruptly
    stop.set()
    pool_t.join(timeout=2)
    sched_t.join(timeout=2)

    # Check what survived
    completed = 0
    for tid in task_ids:
        info = json.loads(check_task(tid))
        if info["status"] == "succeeded":
            completed += 1
    print(f"  Completed before crash: {completed}/{len(task_ids)}")
    print("  *** SIMULATED CRASH — worker killed ***")
    print()

    # ================================================================
    # STEP 4: "Reopen IDE" — resume_session discovers incomplete work
    # ================================================================
    print("=" * 60)
    print("STEP 4: New session — resume_session discovers unfinished tasks")
    print("=" * 60)

    # Reconfigure (simulating fresh process)
    configure(db_path)
    repo2 = SQLiteJobRepository.from_path(db_path)
    direct_queue2 = QueueService(repo2, retry_policy=RetryPolicy(base_delay_seconds=0.1))

    # Recover expired leases first (stale running jobs from crash)
    recovered = direct_queue2.recover_expired_leases()
    print(f"  Recovered {len(recovered)} expired lease(s)")

    result = json.loads(resume_session(CORRELATION_ID))
    print(f"  → {result['message']}")
    for t in result["tasks"]:
        print(f"    {t['type']:15s} status={t['status']:17s} id={t['id'][:8]}...")
    print()

    # ================================================================
    # STEP 5: Resume processing — finish remaining tasks
    # ================================================================
    print("=" * 60)
    print("STEP 5: Resume — process remaining tasks to completion")
    print("=" * 60)

    registry2 = HandlerRegistry()
    for job_type, handler in AI_HANDLERS.items():
        registry2.register(job_type, handler)

    _pool2, stop2, sched_t2, pool_t2 = _make_pool(direct_queue2, registry2)
    sched_t2.start()
    pool_t2.start()

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
    pool_t2.join(timeout=5)
    sched_t2.join(timeout=5)

    # ================================================================
    # Final report
    # ================================================================
    print()
    print("=" * 60)
    print("FINAL REPORT")
    print("=" * 60)

    all_succeeded = True
    for tid in task_ids:
        info = json.loads(check_task(tid))
        status = info["status"]
        if status != "succeeded":
            all_succeeded = False
        print(
            f"  {info['type']:15s} "
            f"status={status:17s} "
            f"attempts={info['attempt']} "
            f"id={info['job_id'][:8]}..."
        )

    h = json.loads(health())
    print(f"\n  Health: {json.dumps(h)}")
    verdict = (
        "\u2713 ALL TASKS COMPLETED \u2014 zero data loss after crash"
        if all_succeeded
        else "\u2717 Some tasks still incomplete"
    )
    print(f"\n  {verdict}")


if __name__ == "__main__":
    main()
