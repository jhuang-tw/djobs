"""djobs — local repository memory and explicit handoff for coding agents.

Public API
----------
>>> from djobs import Job, QueueService, HandlerRegistry, WorkerPool, Daemon
>>> repo = SQLiteJobRepository.from_path("jobs.db")
>>> queue = QueueService(repo)
>>> job = queue.submit("my_task", {"key": "value"})
"""

from __future__ import annotations

__version__ = "0.18.1"

from djobs.core.errors import (
    DJobsError,
    DuplicateHandlerError,
    HandlerNotFoundError,
    InvalidStateTransitionError,
    JobNotFoundError,
    NonRetryableJobError,
    RetryableJobError,
)
from djobs.core.models import Job
from djobs.core.states import JobStatus
from djobs.daemon import Daemon
from djobs.queue.service import QueueService
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.worker.pool import WorkerPool
from djobs.worker.registry import HandlerRegistry
from djobs.worker.runner import WorkerRunner

__all__ = [
    "DJobsError",
    "Daemon",
    "DuplicateHandlerError",
    "HandlerNotFoundError",
    "HandlerRegistry",
    "InvalidStateTransitionError",
    "Job",
    "JobNotFoundError",
    "JobStatus",
    "NonRetryableJobError",
    "QueueService",
    "RetryableJobError",
    "SQLiteJobRepository",
    "WorkerPool",
    "WorkerRunner",
]
