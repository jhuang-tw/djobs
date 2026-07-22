"""Retry policy and backoff calculation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff policy for retryable job failures."""

    base_delay_seconds: float = 1.0
    multiplier: float = 2.0
    max_delay_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.base_delay_seconds <= 0:
            raise ValueError("base_delay_seconds must be greater than 0")
        if self.multiplier < 1:
            raise ValueError("multiplier must be greater than or equal to 1")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError(
                "max_delay_seconds must be greater than or equal to base_delay_seconds"
            )

    def calculate_delay(self, attempt: int) -> timedelta:
        """Return retry delay for the attempt that just failed."""
        if attempt <= 0:
            raise ValueError("attempt must be greater than 0")
        delay_seconds = self.base_delay_seconds * (self.multiplier ** (attempt - 1))
        return timedelta(seconds=min(delay_seconds, self.max_delay_seconds))

    def next_run_after(self, attempt: int, now: datetime | None = None) -> datetime:
        """Return the UTC time when the next retry should become eligible."""
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        return current_time.astimezone(timezone.utc) + self.calculate_delay(attempt)
