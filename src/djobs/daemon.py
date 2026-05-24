"""Background daemon — runs WorkerPool + SchedulerLoop as a standalone process.

Usage::

    from djobs.daemon import Daemon

    daemon = Daemon.from_db("djobs.db", handlers={"echo": echo_handler})
    daemon.run()          # blocks until SIGINT/SIGTERM
    # or
    daemon.run_until(stop_event)
"""

from __future__ import annotations

import logging
import signal
import threading
import uuid
from collections.abc import Callable
from typing import Any

from djobs.queue.service import QueueService
from djobs.scheduler.scheduler import SchedulerLoop
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.worker.pool import WorkerPool
from djobs.worker.registry import HandlerRegistry

logger = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], Any]


class Daemon:
    """Composes WorkerPool + SchedulerLoop into a single long-running process.

    Parameters
    ----------
    queue:
        QueueService backing the daemon.
    registry:
        Pre-populated handler registry.
    worker_id:
        Unique worker identity (auto-generated if omitted).
    max_concurrent:
        Max parallel jobs in the worker pool.
    poll_interval:
        Seconds between job claim attempts.
    scheduler_interval:
        Seconds between scheduler ticks.
    type_concurrency_limits:
        Per-job-type concurrency caps (optional).
    """

    def __init__(
        self,
        queue: QueueService,
        registry: HandlerRegistry,
        *,
        worker_id: str | None = None,
        max_concurrent: int = 4,
        poll_interval: float = 1.0,
        scheduler_interval: float = 5.0,
        type_concurrency_limits: dict[str, int] | None = None,
    ) -> None:
        self._queue = queue
        self._registry = registry
        self._worker_id = worker_id or f"daemon-{uuid.uuid4().hex[:8]}"
        self._max_concurrent = max_concurrent
        self._poll_interval = poll_interval
        self._scheduler_interval = scheduler_interval
        self._type_concurrency_limits = type_concurrency_limits

        self._pool = WorkerPool(
            queue=queue,
            registry=registry,
            worker_id=self._worker_id,
            max_concurrent=max_concurrent,
            type_concurrency_limits=type_concurrency_limits,
            type_filter=list(registry._handlers.keys()),
        )
        self._scheduler = SchedulerLoop(queue=queue)
        self._stop = threading.Event()

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_db(
        cls,
        db_path: str,
        handlers: dict[str, Handler] | None = None,
        **kwargs: Any,
    ) -> Daemon:
        """Create a Daemon from a SQLite database path.

        Parameters
        ----------
        db_path:
            Path to the SQLite database (shared with MCP server).
        handlers:
            Mapping of job_type → handler callable.
        **kwargs:
            Forwarded to ``Daemon.__init__``.
        """
        repo = SQLiteJobRepository.from_path(db_path)
        queue = QueueService(repo)
        registry = HandlerRegistry()
        for job_type, handler in (handlers or {}).items():
            registry.register(job_type, handler)
        return cls(queue, registry, **kwargs)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def worker_id(self) -> str:
        return self._worker_id

    @property
    def pool(self) -> WorkerPool:
        return self._pool

    @property
    def scheduler(self) -> SchedulerLoop:
        return self._scheduler

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Block until SIGINT or SIGTERM, then drain gracefully."""
        self._install_signal_handlers()
        self.run_until(self._stop)

    def run_until(self, stop_event: threading.Event) -> None:
        """Run until *stop_event* is set."""
        self._stop = stop_event

        logger.info(
            "Daemon starting (worker_id=%s, max_concurrent=%d, poll=%.1fs, scheduler=%.1fs)",
            self._worker_id,
            self._max_concurrent,
            self._poll_interval,
            self._scheduler_interval,
        )

        # Scheduler runs in a background thread.
        scheduler_thread = threading.Thread(
            target=self._scheduler.run_loop,
            args=(self._scheduler_interval, stop_event),
            daemon=True,
            name="djobs-scheduler",
        )
        scheduler_thread.start()

        # Worker pool runs in the main thread (blocks).
        try:
            self._pool.run_loop(stop_event, poll_interval=self._poll_interval)
        finally:
            logger.info("Worker pool stopped, waiting for scheduler…")
            stop_event.set()
            scheduler_thread.join(timeout=10)
            logger.info(
                "Daemon stopped. completed=%d failed=%d",
                self._pool.completed_count,
                self._pool.failed_count,
            )

    def stop(self) -> None:
        """Signal the daemon to shut down gracefully."""
        logger.info("Daemon stop requested")
        self._stop.set()

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------

    def _install_signal_handlers(self) -> None:
        """Install SIGINT/SIGTERM handlers to trigger graceful shutdown."""

        def _handler(signum: int, _frame: Any) -> None:
            sig_name = signal.Signals(signum).name
            logger.info("Received %s, shutting down…", sig_name)
            self._stop.set()

        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)
