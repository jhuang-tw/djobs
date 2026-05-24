"""In-process metrics collector for job system observability.

Provides thread-safe counters and a snapshot mechanism.
No external dependencies — designed for Phase 6 single-node use.
A production system would export these to Prometheus, StatsD, etc.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any


class MetricsCollector:
    """Thread-safe counters and gauges for the job system."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}
        self._created_at = datetime.now(UTC)

    # ------------------------------------------------------------------
    # Counter operations (monotonically increasing)
    # ------------------------------------------------------------------

    def inc(self, name: str, amount: int = 1) -> None:
        """Increment a counter by *amount*."""
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + amount

    def counter(self, name: str) -> int:
        """Read current counter value."""
        with self._lock:
            return self._counters.get(name, 0)

    # ------------------------------------------------------------------
    # Gauge operations (point-in-time value)
    # ------------------------------------------------------------------

    def set_gauge(self, name: str, value: float) -> None:
        """Set a gauge to *value*."""
        with self._lock:
            self._gauges[name] = value

    def gauge(self, name: str) -> float:
        """Read current gauge value."""
        with self._lock:
            return self._gauges.get(name, 0.0)

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Return a point-in-time copy of all metrics."""
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "collected_since": self._created_at.isoformat(),
            }

    def reset(self) -> None:
        """Clear all counters and gauges."""
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._created_at = datetime.now(UTC)
