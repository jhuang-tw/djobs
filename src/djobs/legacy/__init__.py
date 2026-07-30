"""Compatibility facade for the original durable queue engine.

New integrations should prefer :class:`djobs.ProjectMemory`.  These exports stay
stable for existing queue users and intentionally do not participate in passive
repository-memory recovery.
"""

from djobs.core.models import Job
from djobs.daemon import Daemon
from djobs.queue.service import QueueService
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.worker.pool import WorkerPool
from djobs.worker.registry import HandlerRegistry
from djobs.worker.runner import WorkerRunner

__all__ = [
    "Daemon",
    "HandlerRegistry",
    "Job",
    "QueueService",
    "SQLiteJobRepository",
    "WorkerPool",
    "WorkerRunner",
]
