"""Domain exceptions."""

from __future__ import annotations


class DJobsError(Exception):
    """Base exception for all djobs errors."""


class InvalidStateTransitionError(DJobsError):
    """Raised when a job state transition is not allowed."""


# Alias for backward compatibility
InvalidStateTransition = InvalidStateTransitionError


class JobNotFoundError(DJobsError):
    """Raised when a job id cannot be found."""


class AgentNotFoundError(DJobsError):
    """Raised when an agent id is not registered."""


class HandlerNotFoundError(DJobsError):
    """Raised when a worker cannot find a handler for a job type."""


class DuplicateHandlerError(DJobsError):
    """Raised when a handler is registered more than once."""


class RetryableJobError(DJobsError):
    """Raised by a handler when a failure should be retried."""


class NonRetryableJobError(DJobsError):
    """Raised by a handler when a failure should become terminal failed."""


class PayloadTooLargeError(DJobsError):
    """Raised when a job payload exceeds the configured size limit."""
