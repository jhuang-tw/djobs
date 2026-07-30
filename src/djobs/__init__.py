"""djobs — local repository memory and explicit handoff for coding agents.

Primary API
-----------
>>> from djobs import ProjectMemory
>>> memory = ProjectMemory.open(cwd="/path/to/repository")
>>> context = memory.sync_workspace(query="Continue the parser fix", context_tier="resume")

Legacy durable queue exports remain available for compatibility.
"""

from __future__ import annotations

from importlib import import_module

__version__ = "0.18.4"

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
from djobs.memory import memory_action
from djobs.project_memory import ProjectMemory
from djobs.queue.service import QueueService
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.worker.pool import WorkerPool
from djobs.worker.registry import HandlerRegistry
from djobs.worker.runner import WorkerRunner

# Preserve ``from djobs import handoff`` as the historical module import. Exporting
# the handoff function under the same name would shadow the module and break users
# that access helpers such as ``handoff.configure``.
handoff = import_module("djobs.handoff")
checkpoint = handoff.checkpoint
handoff_task = handoff.handoff
sync_workspace = handoff.sync_workspace

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
    "ProjectMemory",
    "QueueService",
    "RetryableJobError",
    "SQLiteJobRepository",
    "WorkerPool",
    "WorkerRunner",
    "checkpoint",
    "handoff",
    "handoff_task",
    "memory_action",
    "sync_workspace",
]
