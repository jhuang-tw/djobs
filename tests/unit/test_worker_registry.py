"""Tests for HandlerRegistry."""

from __future__ import annotations

import pytest

from djobs.core.errors import DuplicateHandlerError, HandlerNotFoundError
from djobs.worker.registry import HandlerRegistry


def test_register_and_get_handler() -> None:
    registry = HandlerRegistry()

    def handler(payload: dict[str, object]) -> dict[str, object]:
        return payload

    registry.register("demo.echo", handler)

    assert registry.get("demo.echo") is handler
    assert registry.has("demo.echo") is True


def test_missing_handler_raises() -> None:
    registry = HandlerRegistry()

    with pytest.raises(HandlerNotFoundError):
        registry.get("missing")


def test_duplicate_handler_raises() -> None:
    registry = HandlerRegistry()

    def handler(payload: dict[str, object]) -> dict[str, object]:
        return payload

    registry.register("demo.echo", handler)

    with pytest.raises(DuplicateHandlerError):
        registry.register("demo.echo", handler)
