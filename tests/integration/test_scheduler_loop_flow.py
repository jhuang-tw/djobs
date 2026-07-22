"""Integration test: scheduler loop drives retry promotion + lease recovery."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from djobs.core.states import JobStatus
from djobs.queue.service import QueueService
from djobs.scheduler.scheduler import SchedulerLoop
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.worker.registry import HandlerRegistry
from djobs.worker.runner import WorkerRunner


def test_scheduler_loop_full_lifecycle(tmp_path) -> None:
    """Worker crashes → scheduler recovers lease → worker retries → succeeds."""
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)
    scheduler = SchedulerLoop(queue)
    registry = HandlerRegistry()
    calls: list[dict[str, Any]] = []

    def handler(payload: dict[str, Any]) -> None:
        calls.append(payload)

    registry.register("demo.echo", handler)
    submitted = queue.submit("demo.echo", {"v": 1}, max_attempts=3)

    # Worker-A claims with short lease, then "crashes"
    repo.claim_next_job("worker-A", lease_duration=timedelta(seconds=1))

    # Scheduler tick at far-future recovers the expired lease
    far = datetime.now(timezone.utc) + timedelta(hours=1)
    result = scheduler.tick(now=far)

    assert result.recovered == 1

    # Worker-B picks up and succeeds
    runner = WorkerRunner(queue, registry, worker_id="worker-B")
    run_result = runner.run_once()

    assert run_result.did_run is True
    final = queue.get_job(submitted.id)
    assert final is not None
    assert final.status == JobStatus.SUCCEEDED
    assert calls == [{"v": 1}]

    event_types = [e.event_type for e in queue.events(submitted.id)]
    assert event_types == [
        "job_created",
        "job_claimed",
        "lease_expired",
        "job_claimed",
        "job_succeeded",
    ]


def test_scheduler_loop_retry_promotion_end_to_end(tmp_path) -> None:
    """Job fails with retry → scheduler promotes → worker retries → succeeds."""
    from djobs.core.errors import RetryableJobError

    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)
    scheduler = SchedulerLoop(queue)
    registry = HandlerRegistry()

    attempt_counter = {"n": 0}

    def flaky_handler(payload: dict[str, Any]) -> None:
        attempt_counter["n"] += 1
        if attempt_counter["n"] == 1:
            raise RetryableJobError("transient")

    registry.register("demo.flaky", flaky_handler)
    submitted = queue.submit("demo.flaky", max_attempts=3)

    # First attempt — will raise RetryableJobError
    runner = WorkerRunner(queue, registry, worker_id="w-1")
    result1 = runner.run_once()
    assert result1.error == "transient"

    mid = queue.get_job(submitted.id)
    assert mid is not None
    assert mid.status == JobStatus.RETRY_SCHEDULED

    # Scheduler promotes the retry
    far = datetime.now(timezone.utc) + timedelta(hours=1)
    tick = scheduler.tick(now=far)
    assert tick.promoted == 1

    # Second attempt — will succeed
    result2 = runner.run_once()
    assert result2.did_run is True

    final = queue.get_job(submitted.id)
    assert final is not None
    assert final.status == JobStatus.SUCCEEDED
    assert attempt_counter["n"] == 2


def test_scheduler_threaded_loop_integration(tmp_path) -> None:
    """Scheduler runs in a thread, recovers a crashed job, worker picks up."""
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)
    scheduler = SchedulerLoop(queue)

    submitted = queue.submit("demo.echo", max_attempts=3)
    repo.claim_next_job("worker-crash", lease_duration=timedelta(seconds=1))

    stop = threading.Event()
    tick_count = 0
    original_tick = scheduler.tick

    def _tick_and_stop(now=None):
        nonlocal tick_count
        far = datetime.now(timezone.utc) + timedelta(hours=1)
        r = original_tick(far)
        tick_count += 1
        stop.set()
        return r

    scheduler.tick = _tick_and_stop  # type: ignore[assignment]

    thread = threading.Thread(
        target=scheduler.run_loop,
        kwargs={"interval_seconds": 0.01, "stop_event": stop},
    )
    thread.start()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert tick_count >= 1

    refreshed = queue.get_job(submitted.id)
    assert refreshed is not None
    assert refreshed.status == JobStatus.PENDING
