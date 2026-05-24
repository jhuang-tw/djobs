"""Tests for job state machine transitions."""

import pytest

from djobs.core.errors import InvalidStateTransitionError
from djobs.core.states import JobStatus, validate_transition


class TestValidTransitions:
    """Valid job lifecycle transitions."""

    @pytest.mark.parametrize(
        ("from_status", "to_status"),
        [
            (JobStatus.PENDING, JobStatus.RUNNING),
            (JobStatus.PENDING, JobStatus.SUCCEEDED),  # AI agent direct complete
            (JobStatus.PENDING, JobStatus.FAILED),  # AI agent direct fail
            (JobStatus.RUNNING, JobStatus.SUCCEEDED),
            (JobStatus.RUNNING, JobStatus.FAILED),
            (JobStatus.RUNNING, JobStatus.RETRY_SCHEDULED),
            (JobStatus.RUNNING, JobStatus.DEAD_LETTERED),
            (JobStatus.RUNNING, JobStatus.PENDING),  # lease recovery
            (JobStatus.RETRY_SCHEDULED, JobStatus.PENDING),
        ],
    )
    def test_allowed(self, from_status: JobStatus, to_status: JobStatus) -> None:
        validate_transition(from_status, to_status)  # should not raise


class TestInvalidTransitions:
    """Transitions that must be rejected."""

    @pytest.mark.parametrize(
        ("from_status", "to_status"),
        [
            (JobStatus.PENDING, JobStatus.RETRY_SCHEDULED),
            (JobStatus.SUCCEEDED, JobStatus.RUNNING),
            (JobStatus.SUCCEEDED, JobStatus.FAILED),
            (JobStatus.SUCCEEDED, JobStatus.RETRY_SCHEDULED),
            (JobStatus.FAILED, JobStatus.RUNNING),
            (JobStatus.FAILED, JobStatus.PENDING),
            (JobStatus.DEAD_LETTERED, JobStatus.PENDING),
        ],
    )
    def test_rejected(self, from_status: JobStatus, to_status: JobStatus) -> None:
        with pytest.raises(InvalidStateTransitionError):
            validate_transition(from_status, to_status)
