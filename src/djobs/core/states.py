"""Job status enum and state transition validation."""

from __future__ import annotations

import enum


class JobStatus(enum.StrEnum):
    """All possible job states.

    Phase 1 transitions:
        pending -> running -> succeeded
        pending -> running -> failed

    Phase 2 transitions:
        running -> retry_scheduled -> pending
        running -> dead_lettered

    Phase 3 transitions (lease recovery):
        running -> pending  (only via recover_expired_leases)
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRY_SCHEDULED = "retry_scheduled"
    DEAD_LETTERED = "dead_lettered"
    ARCHIVED = "archived"


class AgentStatus(enum.StrEnum):
    """Liveness state of a registered agent (Phase M4: agent registry).

    online  -> the agent has heartbeated recently and can take work.
    offline -> the agent stopped heartbeating and was reaped.
    """

    ONLINE = "online"
    OFFLINE = "offline"


# Valid (from_status -> to_status) pairs.
_VALID_TRANSITIONS: set[tuple[JobStatus, JobStatus]] = {
    (JobStatus.PENDING, JobStatus.RUNNING),
    (JobStatus.PENDING, JobStatus.SUCCEEDED),  # AI agent completes without claim
    (JobStatus.PENDING, JobStatus.FAILED),  # AI agent fails without claim
    (JobStatus.RUNNING, JobStatus.SUCCEEDED),
    (JobStatus.RUNNING, JobStatus.FAILED),
    (JobStatus.RUNNING, JobStatus.RETRY_SCHEDULED),
    (JobStatus.RUNNING, JobStatus.DEAD_LETTERED),
    (JobStatus.PENDING, JobStatus.ARCHIVED),
    (JobStatus.RUNNING, JobStatus.ARCHIVED),
    (JobStatus.RETRY_SCHEDULED, JobStatus.ARCHIVED),
    (JobStatus.FAILED, JobStatus.ARCHIVED),
    (JobStatus.DEAD_LETTERED, JobStatus.ARCHIVED),
    (JobStatus.SUCCEEDED, JobStatus.ARCHIVED),  # archive completed work for cleanup
    (JobStatus.RUNNING, JobStatus.PENDING),  # lease recovery
    (JobStatus.RETRY_SCHEDULED, JobStatus.PENDING),
}


def validate_transition(from_status: JobStatus, to_status: JobStatus) -> None:
    """Raise InvalidStateTransition if the transition is not allowed."""
    from djobs.core.errors import InvalidStateTransitionError

    if (from_status, to_status) not in _VALID_TRANSITIONS:
        raise InvalidStateTransitionError(
            f"Cannot transition from {from_status.value!r} to {to_status.value!r}"
        )
