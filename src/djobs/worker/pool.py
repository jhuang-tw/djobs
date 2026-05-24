"""Worker pool with concurrency control and graceful drain.

Runs up to *max_concurrent* jobs in parallel using a thread pool.
Supports per-job-type concurrency limits and graceful shutdown.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TYPE_CHECKING

from djobs.core.errors import RetryableJobError
from djobs.core.models import Job
from djobs.worker.registry import HandlerRegistry

if TYPE_CHECKING:
    from djobs.queue.service import QueueService

logger = logging.getLogger(__name__)


class WorkerPool:
    """Concurrent worker pool with graceful drain.

    Parameters
    ----------
    queue:
        QueueService for claiming and reporting job results.
    registry:
        Handler registry mapping job types to callables.
    worker_id:
        Identifier for this worker pool (used in lease).
    max_concurrent:
        Maximum number of jobs to execute in parallel.
    type_concurrency_limits:
        Optional per-job-type concurrency limits.
        E.g. ``{"send_email": 3}`` means at most 3 ``send_email`` jobs running.
    """

    def __init__(
        self,
        queue: QueueService,
        registry: HandlerRegistry,
        worker_id: str,
        max_concurrent: int = 1,
        type_concurrency_limits: dict[str, int] | None = None,
        type_filter: list[str] | None = None,
    ) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        self._queue = queue
        self._registry = registry
        self._worker_id = worker_id
        self._max_concurrent = max_concurrent
        self._type_concurrency_limits = type_concurrency_limits
        self._type_filter = type_filter

        self._lock = threading.Lock()
        self._active: set[Future[None]] = set()
        self._completed_count = 0
        self._failed_count = 0

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._active)

    @property
    def completed_count(self) -> int:
        with self._lock:
            return self._completed_count

    @property
    def failed_count(self) -> int:
        with self._lock:
            return self._failed_count

    # ------------------------------------------------------------------
    # Run loop
    # ------------------------------------------------------------------

    def run_loop(
        self,
        stop_event: threading.Event,
        poll_interval: float = 1.0,
    ) -> None:
        """Poll for jobs and dispatch them until *stop_event* is set.

        When *stop_event* is set the pool stops claiming new jobs and
        waits for all in-flight jobs to finish (graceful drain).
        """
        logger.info(
            "WorkerPool starting (max_concurrent=%d, worker_id=%s)",
            self._max_concurrent,
            self._worker_id,
        )

        executor = ThreadPoolExecutor(max_workers=self._max_concurrent)
        try:
            while not stop_event.is_set():
                self._reap_done_futures()

                if self.active_count >= self._max_concurrent:
                    stop_event.wait(timeout=poll_interval)
                    continue

                job = self._queue.claim(
                    self._worker_id,
                    type_concurrency_limits=self._type_concurrency_limits,
                    type_filter=self._type_filter,
                )
                if job is None:
                    stop_event.wait(timeout=poll_interval)
                    continue

                future = executor.submit(self._execute_job, job)
                with self._lock:
                    self._active.add(future)
        finally:
            # Graceful drain: wait for in-flight jobs
            logger.info(
                "WorkerPool draining %d in-flight jobs",
                self.active_count,
            )
            executor.shutdown(wait=True)
            self._reap_done_futures()
            logger.info("WorkerPool stopped")

    # ------------------------------------------------------------------
    # Job execution (runs in thread pool)
    # ------------------------------------------------------------------

    def _execute_job(self, job: Job) -> None:
        """Execute a single job's handler and record result."""
        t0 = time.monotonic()
        try:
            handler = self._registry.get(job.type)
            handler(job.payload)
        except RetryableJobError as exc:
            error = str(exc) or exc.__class__.__name__
            self._queue.retry_or_dead_letter(job.id, error)
            elapsed = time.monotonic() - t0
            logger.warning(
                "Job %s retryable error: %s (%.3fs)",
                job.id,
                error,
                elapsed,
                extra={"job_id": job.id, "worker_id": self._worker_id, "duration_s": elapsed},
            )
            with self._lock:
                self._failed_count += 1
            return
        except Exception as exc:
            error = str(exc) or exc.__class__.__name__
            self._queue.fail(job.id, error)
            elapsed = time.monotonic() - t0
            logger.error(
                "Job %s failed: %s (%.3fs)",
                job.id,
                error,
                elapsed,
                extra={"job_id": job.id, "worker_id": self._worker_id, "duration_s": elapsed},
            )
            with self._lock:
                self._failed_count += 1
            return

        try:
            self._queue.complete(job.id)
        except Exception:
            logger.exception("Job %s failed to record completion", job.id)
            with self._lock:
                self._failed_count += 1
            return

        elapsed = time.monotonic() - t0
        logger.info(
            "Job %s succeeded (%.3fs)",
            job.id,
            elapsed,
            extra={"job_id": job.id, "worker_id": self._worker_id, "duration_s": elapsed},
        )
        with self._lock:
            self._completed_count += 1

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _reap_done_futures(self) -> None:
        with self._lock:
            done = {f for f in self._active if f.done()}
            self._active -= done
