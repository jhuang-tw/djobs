"""Smoke test: verify all packages can be imported."""

import importlib

import pytest

# One representative module per subpackage — a smoke test that the package and
# its public entry points import cleanly, not an exhaustive module inventory.
MODULES = [
    "djobs",
    "djobs.cli",
    "djobs.mcp_server",
    "djobs.api.ai_handlers",
    "djobs.core.models",
    "djobs.observability.metrics",
    "djobs.queue.service",
    "djobs.scheduler.scheduler",
    "djobs.storage.sqlite",
    "djobs.worker.pool",
]


@pytest.mark.parametrize("module_name", MODULES)
def test_import(module_name: str) -> None:
    mod = importlib.import_module(module_name)
    assert mod is not None


def test_version() -> None:
    import re

    import djobs

    # Version is sourced solely from src/djobs/__init__.py (__version__);
    # assert shape only so releases never require editing this test.
    assert re.fullmatch(r"\d+\.\d+\.\d+", djobs.__version__)


def test_legacy_facade_preserves_queue_exports() -> None:
    import djobs
    import djobs.legacy as legacy

    assert legacy.Job is djobs.Job
    assert legacy.QueueService is djobs.QueueService
    assert legacy.SQLiteJobRepository is djobs.SQLiteJobRepository
    assert legacy.WorkerPool is djobs.WorkerPool
