"""Job domain model."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from djobs.core.states import JobStatus


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid.uuid4())


@dataclass
class Job:
    """A durable unit of work.

    Fields cover Phase 1 (basic lifecycle), Phase 2 (retry, idempotency),
    Phase 3 (lease, heartbeat, crash recovery), and Phase 6 (correlation id,
    execution duration).
    """

    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=_new_id)
    status: JobStatus = JobStatus.PENDING
    attempt: int = 0
    max_attempts: int = 1
    run_after: datetime | None = None
    idempotency_key: str | None = None
    correlation_id: str = field(default_factory=_new_id)
    last_error: str | None = None
    leased_by: str | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    started_at: datetime | None = None
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
