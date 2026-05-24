"""Tests for WorkerPool."""

from __future__ import annotations

import threading
import time
from typing import Any

from djobs.core.errors import RetryableJobError
from djobs.core.states import JobStatus
from djobs.queue.service import QueueService
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.worker.pool import WorkerPool
from djobs.worker.registry import HandlerRegistry


def _setup(tmp_path, max_concurrent=1, type_concurrency_limits=None):
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)
    registry = HandlerRegistry()
    pool = WorkerPool(
        queue,
        registry,
        worker_id="pool-1",
        max_concurrent=max_concurrent,
        type_concurrency_limits=type_concurrency_limits,
    )
    return queue, registry, pool


# ------------------------------------------------------------------
# Basic lifecycle
# ------------------------------------------------------------------


def test_pool_processes_single_job(tmp_path) -> None:
    queue, registry, pool = _setup(tmp_path)
    results: list[dict[str, Any]] = []
    registry.register("echo", lambda p: results.append(p))

    queue.submit("echo", {"v": 1})

    stop = threading.Event()

    def _stop_after_processed():
        while not results:
            time.sleep(0.01)
        stop.set()

    threading.Thread(target=_stop_after_processed, daemon=True).start()
    pool.run_loop(stop, poll_interval=0.01)

    assert results == [{"v": 1}]
    assert pool.completed_count == 1
    assert pool.active_count == 0


def test_pool_processes_multiple_jobs_concurrently(tmp_path) -> None:
    queue, registry, pool = _setup(tmp_path, max_concurrent=3)

    started = threading.Event()
    gate = threading.Event()
    call_count = {"n": 0}
    lock = threading.Lock()

    def slow_handler(payload: dict[str, Any]) -> None:
        with lock:
            call_count["n"] += 1
            if call_count["n"] >= 3:
                started.set()
        gate.wait(timeout=5)

    registry.register("slow", slow_handler)

    for i in range(3):
        queue.submit("slow", {"i": i})

    stop = threading.Event()
    thread = threading.Thread(target=pool.run_loop, args=(stop,), kwargs={"poll_interval": 0.01})
    thread.start()

    # Wait until all 3 handlers are running concurrently
    assert started.wait(timeout=5), "Not all 3 jobs started concurrently"

    # Release handlers, give them time to complete, then stop
    gate.set()
    time.sleep(0.5)
    stop.set()
    thread.join(timeout=5)

    assert call_count["n"] == 3
    assert pool.completed_count + pool.failed_count == 3


def test_pool_respects_max_concurrent(tmp_path) -> None:
    queue, registry, pool = _setup(tmp_path, max_concurrent=1)

    gate = threading.Event()
    concurrent = {"max": 0, "current": 0}
    lock = threading.Lock()

    def tracking_handler(payload: dict[str, Any]) -> None:
        with lock:
            concurrent["current"] += 1
            concurrent["max"] = max(concurrent["max"], concurrent["current"])
        gate.wait(timeout=5)
        with lock:
            concurrent["current"] -= 1

    registry.register("track", tracking_handler)
    queue.submit("track", {"i": 1})
    queue.submit("track", {"i": 2})

    stop = threading.Event()

    def _stop_after():
        time.sleep(0.2)
        gate.set()
        time.sleep(0.3)
        stop.set()

    threading.Thread(target=_stop_after, daemon=True).start()

    pool.run_loop(stop, poll_interval=0.01)
    assert concurrent["max"] == 1


# ------------------------------------------------------------------
# Error handling
# ------------------------------------------------------------------


def test_pool_handles_retryable_error(tmp_path) -> None:
    queue, registry, pool = _setup(tmp_path)
    registry.register("fail", lambda p: (_ for _ in ()).throw(RetryableJobError("boom")))

    submitted = queue.submit("fail", max_attempts=3)

    stop = threading.Event()

    def _stop_after():
        time.sleep(0.3)
        stop.set()

    threading.Thread(target=_stop_after, daemon=True).start()
    pool.run_loop(stop, poll_interval=0.01)

    job = queue.get_job(submitted.id)
    assert job is not None
    assert job.status == JobStatus.RETRY_SCHEDULED
    assert pool.failed_count >= 1


def test_pool_handles_generic_exception(tmp_path) -> None:
    queue, registry, pool = _setup(tmp_path)

    def bad_handler(p):
        raise RuntimeError("crash")

    registry.register("bad", bad_handler)
    submitted = queue.submit("bad")

    stop = threading.Event()

    def _stop_after():
        time.sleep(0.3)
        stop.set()

    threading.Thread(target=_stop_after, daemon=True).start()
    pool.run_loop(stop, poll_interval=0.01)

    job = queue.get_job(submitted.id)
    assert job is not None
    assert job.status == JobStatus.FAILED
    assert pool.failed_count >= 1


# ------------------------------------------------------------------
# Graceful drain
# ------------------------------------------------------------------


def test_pool_drains_on_stop(tmp_path) -> None:
    """When stop is set, pool finishes in-flight jobs before returning."""
    queue, registry, pool = _setup(tmp_path, max_concurrent=2)

    gate = threading.Event()
    finished = []

    def slow_handler(payload: dict[str, Any]) -> None:
        gate.wait(timeout=5)
        finished.append(payload["i"])

    registry.register("slow", slow_handler)
    queue.submit("slow", {"i": 1})
    queue.submit("slow", {"i": 2})

    stop = threading.Event()
    thread = threading.Thread(target=pool.run_loop, args=(stop,), kwargs={"poll_interval": 0.01})
    thread.start()

    time.sleep(0.2)  # Let jobs start
    stop.set()  # Signal stop (but jobs are still running)
    time.sleep(0.1)
    gate.set()  # Release jobs

    thread.join(timeout=5)
    assert not thread.is_alive()
    assert sorted(finished) == [1, 2]  # Both jobs completed despite stop


# ------------------------------------------------------------------
# Validation
# ------------------------------------------------------------------


def test_pool_rejects_invalid_max_concurrent() -> None:
    import pytest

    with pytest.raises(ValueError, match="max_concurrent"):
        WorkerPool(
            queue=None,  # type: ignore[arg-type]
            registry=None,  # type: ignore[arg-type]
            worker_id="x",
            max_concurrent=0,
        )


# ------------------------------------------------------------------
# Idle pool
# ------------------------------------------------------------------


def test_pool_idle_no_jobs(tmp_path) -> None:
    _queue, registry, pool = _setup(tmp_path)
    registry.register("echo", lambda p: None)

    stop = threading.Event()

    def _stop_after():
        time.sleep(0.1)
        stop.set()

    threading.Thread(target=_stop_after, daemon=True).start()
    pool.run_loop(stop, poll_interval=0.01)

    assert pool.completed_count == 0
    assert pool.active_count == 0
