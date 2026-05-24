"""Integration test: WorkerPool processes jobs with concurrency + scheduler."""

from __future__ import annotations

import threading
import time
from typing import Any

from djobs.core.errors import RetryableJobError
from djobs.core.states import JobStatus
from djobs.queue.service import QueueService
from djobs.scheduler.scheduler import SchedulerLoop
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.worker.pool import WorkerPool
from djobs.worker.registry import HandlerRegistry


def test_pool_with_scheduler_retry_flow(tmp_path) -> None:
    """Pool + scheduler: job fails → scheduler promotes retry → pool retries → succeeds."""
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)
    scheduler = SchedulerLoop(queue)
    registry = HandlerRegistry()

    attempt_counter = {"n": 0}

    def flaky(payload: dict[str, Any]) -> None:
        attempt_counter["n"] += 1
        if attempt_counter["n"] == 1:
            raise RetryableJobError("transient")

    registry.register("flaky", flaky)
    submitted = queue.submit("flaky", {"v": 1}, max_attempts=3)

    stop = threading.Event()
    pool = WorkerPool(queue, registry, "w-1", max_concurrent=2)

    # Run pool briefly to process first attempt
    def _run_pool():
        pool.run_loop(stop, poll_interval=0.01)

    thread = threading.Thread(target=_run_pool)
    thread.start()
    time.sleep(0.5)
    stop.set()
    thread.join(timeout=5)

    # Job should be retry_scheduled after first attempt
    mid = queue.get_job(submitted.id)
    assert mid is not None
    assert mid.status == JobStatus.RETRY_SCHEDULED

    # Scheduler promotes
    from datetime import UTC, datetime, timedelta

    scheduler.tick(now=datetime.now(UTC) + timedelta(hours=1))

    promoted = queue.get_job(submitted.id)
    assert promoted is not None
    assert promoted.status == JobStatus.PENDING

    # Run pool again for second attempt
    stop2 = threading.Event()
    pool2 = WorkerPool(queue, registry, "w-2", max_concurrent=1)
    thread2 = threading.Thread(
        target=pool2.run_loop,
        args=(stop2,),
        kwargs={"poll_interval": 0.01},
    )
    thread2.start()
    time.sleep(0.5)
    stop2.set()
    thread2.join(timeout=5)

    final = queue.get_job(submitted.id)
    assert final is not None
    assert final.status == JobStatus.SUCCEEDED
    assert attempt_counter["n"] == 2


def test_pool_type_concurrency_integration(tmp_path) -> None:
    """Per-type concurrency limit prevents claiming too many of one type."""
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)
    registry = HandlerRegistry()

    gate = threading.Event()
    started = {"email": 0, "sms": 0}
    lock = threading.Lock()

    def handler(payload: dict[str, Any]) -> None:
        with lock:
            started[payload["type"]] += 1
        gate.wait(timeout=5)

    registry.register("email", handler)
    registry.register("sms", handler)

    # Submit 3 emails and 2 sms
    for _ in range(3):
        queue.submit("email", {"type": "email"})
    for _ in range(2):
        queue.submit("sms", {"type": "sms"})

    pool = WorkerPool(
        queue,
        registry,
        "w-1",
        max_concurrent=5,
        type_concurrency_limits={"email": 1},
    )

    stop = threading.Event()
    thread = threading.Thread(target=pool.run_loop, args=(stop,), kwargs={"poll_interval": 0.01})
    thread.start()

    time.sleep(0.5)

    with lock:
        # At most 1 email running, but sms is unrestricted
        assert started["email"] <= 1
        assert started["sms"] == 2

    gate.set()
    stop.set()
    thread.join(timeout=5)


def test_pool_backlog_metrics(tmp_path) -> None:
    """Backlog metrics reflect current queue state."""
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)

    queue.submit("a")
    queue.submit("b")
    queue.submit("c")
    queue.claim("w-1")

    backlog = queue.backlog()
    assert backlog["pending"] == 2
    assert backlog["running"] == 1
    assert queue.count_running_by_type("a") == 1
    assert queue.count_running_by_type("b") == 0
