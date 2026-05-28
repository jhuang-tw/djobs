"""Queue service for job lifecycle operations."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Protocol

from djobs.core.errors import JobNotFoundError
from djobs.core.models import Job
from djobs.core.retry import RetryPolicy
from djobs.observability.inspect import inspect_job
from djobs.storage.sqlite import JobEvent


class JobRepository(Protocol):
    def create_job(self, job: Job) -> Job: ...
    def get_job(self, job_id: str) -> Job | None: ...
    def find_active_by_idempotency_key(self, idempotency_key: str) -> Job | None: ...
    def claim_next_job(
        self,
        worker_id: str,
        lease_duration: timedelta = ...,
        type_concurrency_limits: dict[str, int] | None = ...,
        type_filter: list[str] | None = ...,
    ) -> Job | None: ...
    def mark_succeeded(self, job_id: str, *, evidence: str | None = None) -> Job: ...
    def mark_failed(self, job_id: str, error: str) -> Job: ...
    def mark_retry_scheduled(self, job_id: str, error: str, run_after: datetime) -> Job: ...
    def mark_dead_lettered(self, job_id: str, error: str) -> Job: ...
    def promote_due_retries(self, now: datetime | None = None) -> list[Job]: ...
    def heartbeat(self, job_id: str, worker_id: str, lease_duration: timedelta = ...) -> Job: ...
    def recover_expired_leases(self, now: datetime | None = None) -> list[Job]: ...
    def count_by_status(self) -> dict[str, int]: ...
    def count_running_by_type(self, job_type: str) -> int: ...
    def list_by_status(self, status: str, limit: int = 100) -> list[Job]: ...
    def list_events(self, job_id: str | None = None) -> list[JobEvent]: ...
    def count_stuck_running(self, now: datetime | None = None) -> int: ...


class QueueService:
    """Coordinates job submission, retry, and lifecycle transitions."""

    def __init__(self, repository: JobRepository, retry_policy: RetryPolicy | None = None) -> None:
        self._repository = repository
        self._retry_policy = retry_policy or RetryPolicy()

    def submit(
        self,
        job_type: str,
        payload: dict[str, Any] | None = None,
        *,
        max_attempts: int = 1,
        run_after: datetime | None = None,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> Job:
        if idempotency_key is not None:
            existing_job = self._repository.find_active_by_idempotency_key(idempotency_key)
            if existing_job is not None:
                return existing_job

        kwargs: dict[str, Any] = {
            "type": job_type,
            "payload": payload or {},
            "max_attempts": max_attempts,
            "run_after": run_after,
            "idempotency_key": idempotency_key,
        }
        if correlation_id is not None:
            kwargs["correlation_id"] = correlation_id

        job = Job(**kwargs)
        return self._repository.create_job(job)

    def submit_batch(
        self,
        jobs: list[dict[str, Any]],
        *,
        correlation_id: str | None = None,
    ) -> list[Job]:
        """Submit multiple jobs at once, sharing a correlation_id.

        Each dict in *jobs* must have ``type`` and optionally ``payload``,
        ``max_attempts``, ``run_after``, ``idempotency_key``.
        """
        created: list[Job] = []
        for spec in jobs:
            j = self.submit(
                job_type=spec["type"],
                payload=spec.get("payload"),
                max_attempts=spec.get("max_attempts", 1),
                run_after=spec.get("run_after"),
                idempotency_key=spec.get("idempotency_key"),
                correlation_id=correlation_id,
            )
            created.append(j)
        return created

    def get_job(self, job_id: str) -> Job | None:
        return self._repository.get_job(job_id)

    def claim(
        self,
        worker_id: str,
        type_concurrency_limits: dict[str, int] | None = None,
        type_filter: list[str] | None = None,
    ) -> Job | None:
        return self._repository.claim_next_job(
            worker_id,
            type_concurrency_limits=type_concurrency_limits,
            type_filter=type_filter,
        )

    def complete(self, job_id: str, *, evidence: str | None = None) -> Job:
        return self._repository.mark_succeeded(job_id, evidence=evidence)

    def fail(self, job_id: str, error: str) -> Job:
        return self._repository.mark_failed(job_id, error)

    def retry_or_dead_letter(
        self,
        job_id: str,
        error: str,
        *,
        now: datetime | None = None,
    ) -> Job:
        job = self._repository.get_job(job_id)
        if job is None:
            raise JobNotFoundError(f"Job {job_id!r} was not found")
        if job.attempt < job.max_attempts:
            run_after = self._retry_policy.next_run_after(job.attempt, now=now)
            return self._repository.mark_retry_scheduled(job_id, error, run_after)
        return self._repository.mark_dead_lettered(job_id, error)

    def promote_due_retries(self, now: datetime | None = None) -> list[Job]:
        return self._repository.promote_due_retries(now)

    def heartbeat(
        self,
        job_id: str,
        worker_id: str,
        lease_duration: timedelta | None = None,
    ) -> Job:
        if lease_duration is not None:
            return self._repository.heartbeat(job_id, worker_id, lease_duration)
        return self._repository.heartbeat(job_id, worker_id)

    def recover_expired_leases(self, now: datetime | None = None) -> list[Job]:
        return self._repository.recover_expired_leases(now)

    def backlog(self) -> dict[str, int]:
        return self._repository.count_by_status()

    def count_running_by_type(self, job_type: str) -> int:
        return self._repository.count_running_by_type(job_type)

    def events(self, job_id: str | None = None) -> list[JobEvent]:
        return self._repository.list_events(job_id)

    def inspect(self, job_id: str) -> dict[str, Any]:
        """Return a detailed inspection summary for a job."""
        job = self._repository.get_job(job_id)
        if job is None:
            raise JobNotFoundError(f"Job {job_id!r} was not found")
        job_events = self._repository.list_events(job_id)
        return inspect_job(job, job_events)

    def health(self) -> dict[str, Any]:
        """Return a health summary: queue depth by status + stuck running tasks."""
        counts = self._repository.count_by_status()
        stuck = self._repository.count_stuck_running()
        result: dict[str, Any] = {
            "status": "ok",
            "queue_depth": counts,
            "total_jobs": sum(counts.values()),
        }
        if stuck > 0:
            result["stuck_running"] = stuck
            result["status"] = "warning"
        return result

    def list_by_status(self, status: str, limit: int = 100) -> list[Job]:
        """Return jobs with the given status string (e.g. 'dead_lettered')."""
        return self._repository.list_by_status(status, limit=limit)
