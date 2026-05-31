"""Smoke test: verify all packages can be imported."""

import importlib

import pytest

MODULES = [
    "djobs",
    "djobs.api",
    "djobs.api.ai_handlers",
    "djobs.core",
    "djobs.core.config",
    "djobs.core.errors",
    "djobs.core.models",
    "djobs.core.retry",
    "djobs.core.states",
    "djobs.observability",
    "djobs.observability.inspect",
    "djobs.observability.logging",
    "djobs.observability.metrics",
    "djobs.queue",
    "djobs.queue.service",
    "djobs.scheduler",
    "djobs.scheduler.scheduler",
    "djobs.storage",
    "djobs.storage.events",
    "djobs.storage.sqlite",
    "djobs.worker",
    "djobs.worker.pool",
    "djobs.worker.registry",
    "djobs.worker.runner",
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
