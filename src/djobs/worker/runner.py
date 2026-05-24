"""Worker runner for executing one claimed job at a time."""

from __future__ import annotations

from dataclasses import dataclass

from djobs.core.errors import RetryableJobError
from djobs.core.models import Job
from djobs.queue.service import QueueService
from djobs.worker.registry import HandlerRegistry


@dataclass(frozen=True)
class RunOnceResult:
    """Result of a single worker polling/execution attempt."""

    job: Job | None
    did_run: bool
    error: str | None = None


class WorkerRunner:
    """Claims one job, runs its handler, and records succeeded/failed state."""

    def __init__(self, queue: QueueService, registry: HandlerRegistry, worker_id: str) -> None:
        self._queue = queue
        self._registry = registry
        self._worker_id = worker_id

    def run_once(self) -> RunOnceResult:
        job = self._queue.claim(self._worker_id)
        if job is None:
            return RunOnceResult(job=None, did_run=False)

        try:
            handler = self._registry.get(job.type)
            handler(job.payload)
        except RetryableJobError as exc:
            error = str(exc) or exc.__class__.__name__
            retried_job = self._queue.retry_or_dead_letter(job.id, error)
            return RunOnceResult(job=retried_job, did_run=True, error=error)
        except Exception as exc:
            error = str(exc) or exc.__class__.__name__
            failed_job = self._queue.fail(job.id, error)
            return RunOnceResult(job=failed_job, did_run=True, error=error)

        succeeded_job = self._queue.complete(job.id)
        return RunOnceResult(job=succeeded_job, did_run=True)
