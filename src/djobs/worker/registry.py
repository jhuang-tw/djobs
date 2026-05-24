"""Handler registry for worker job execution."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from djobs.core.errors import DuplicateHandlerError, HandlerNotFoundError

Handler = Callable[[dict[str, Any]], Any]


class HandlerRegistry:
    """Maps job type names to Python callables."""

    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}

    def register(self, job_type: str, handler: Handler) -> None:
        if job_type in self._handlers:
            raise DuplicateHandlerError(f"Handler {job_type!r} is already registered")
        self._handlers[job_type] = handler

    def get(self, job_type: str) -> Handler:
        try:
            return self._handlers[job_type]
        except KeyError as exc:
            raise HandlerNotFoundError(f"No handler registered for job type {job_type!r}") from exc

    def has(self, job_type: str) -> bool:
        return job_type in self._handlers
