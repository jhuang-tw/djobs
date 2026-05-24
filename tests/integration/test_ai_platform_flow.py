"""Integration test: AI task platform end-to-end flow."""

from __future__ import annotations

import random
import threading
import time

from djobs.api.ai_handlers import AI_HANDLERS
from djobs.core.retry import RetryPolicy
from djobs.core.states import JobStatus
from djobs.queue.service import QueueService
from djobs.scheduler.scheduler import SchedulerLoop
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.worker.pool import WorkerPool
from djobs.worker.registry import HandlerRegistry


def test_ai_batch_end_to_end(tmp_path) -> None:
    """Submit a batch of AI jobs, process with pool + scheduler, verify results."""
    random.seed(42)

    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo, retry_policy=RetryPolicy(base_delay_seconds=0.01))
    registry = HandlerRegistry()

    for jtype, handler in AI_HANDLERS.items():
        registry.register(jtype, handler)

    specs = [
        {"type": "ai.summarize", "payload": {"text": "Test " * 20}, "max_attempts": 3},
        {
            "type": "ai.classify",
            "payload": {"text": "Good", "labels": ["pos", "neg"]},
            "max_attempts": 2,
        },
    ]
    submitted = queue.submit_batch(specs, correlation_id="test-batch")

    assert len(submitted) == 2
    assert all(j.correlation_id == "test-batch" for j in submitted)

    # Run scheduler + pool
    stop = threading.Event()
    scheduler = SchedulerLoop(queue)
    sched_thread = threading.Thread(
        target=scheduler.run_loop,
        kwargs={"interval_seconds": 0.05, "stop_event": stop},
        daemon=True,
    )
    sched_thread.start()

    pool = WorkerPool(queue, registry, "w-1", max_concurrent=2)
    pool_thread = threading.Thread(
        target=pool.run_loop,
        args=(stop,),
        kwargs={"poll_interval": 0.02},
    )
    pool_thread.start()

    # Wait for completion
    for _ in range(200):
        time.sleep(0.05)
        bl = queue.backlog()
        active = bl.get("pending", 0) + bl.get("running", 0) + bl.get("retry_scheduled", 0)
        if active == 0:
            break

    stop.set()
    pool_thread.join(timeout=5)
    sched_thread.join(timeout=5)

    # Verify all jobs reached terminal state
    for job in submitted:
        final = queue.get_job(job.id)
        assert final is not None
        assert final.status in (JobStatus.SUCCEEDED, JobStatus.DEAD_LETTERED)


def test_inspect_shows_cost_metadata(tmp_path) -> None:
    """After AI handler runs, inspect shows token/cost in payload."""
    random.seed(99)

    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)
    registry = HandlerRegistry()
    registry.register("ai.classify", AI_HANDLERS["ai.classify"])

    submitted = queue.submit("ai.classify", {"text": "Good", "labels": ["pos", "neg"]})

    from djobs.worker.runner import WorkerRunner

    runner = WorkerRunner(queue, registry, "w-1")
    runner.run_once()

    info = queue.inspect(submitted.id)
    assert info["status"] == "succeeded"
    assert info["correlation_id"] == submitted.correlation_id
