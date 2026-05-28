"""Job inspection utilities for debugging and observability."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from djobs.core.models import Job
from djobs.storage.sqlite import JobEvent


def inspect_job(job: Job, events: list[JobEvent]) -> dict[str, Any]:
    """Build a human-readable inspection summary for a job.

    Returns a dict suitable for JSON serialisation or terminal display.
    Answers: what happened, how many attempts, last error, duration, next step.
    """
    duration_seconds: float | None = None
    if job.started_at is not None:
        end = job.updated_at or datetime.now(UTC)
        duration_seconds = (end - job.started_at).total_seconds()

    event_timeline = [
        {
            "event": e.event_type,
            "at": e.created_at.isoformat() if e.created_at else None,
            "message": e.message,
        }
        for e in events
    ]

    # Extract evidence from the job_succeeded event (if any).
    evidence: str | None = None
    for e in events:
        if e.event_type == "job_succeeded" and e.metadata.get("evidence"):
            evidence = e.metadata["evidence"]
            break

    # Detect stuck running task (lease expired but still marked running).
    stuck = False
    if (
        job.status.value == "running"
        and job.lease_expires_at is not None
        and job.lease_expires_at < datetime.now(UTC)
    ):
        stuck = True

    result: dict[str, Any] = {
        "job_id": job.id,
        "type": job.type,
        "status": job.status.value,
        "correlation_id": job.correlation_id,
        "attempt": job.attempt,
        "max_attempts": job.max_attempts,
        "last_error": job.last_error,
        "duration_seconds": duration_seconds,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "leased_by": job.leased_by,
        "events": event_timeline,
        "event_count": len(events),
    }
    if evidence is not None:
        result["evidence"] = evidence
    if stuck:
        result["stuck"] = True
        result["warning"] = (
            "This task's lease has expired but it is still marked as running. "
            "The worker may have crashed. Run lease recovery or resume_session "
            "to reclaim it."
        )
    return result
