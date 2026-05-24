"""Event tracking and analytics aggregation for usage metrics."""

from collections import Counter
from datetime import datetime

class EventTracker:
    def __init__(self):
        self._events: list[dict] = []

    def track(self, event_type: str, metadata: dict | None = None) -> None:
        self._events.append({
            "type": event_type,
            "metadata": metadata or {},
            "timestamp": datetime.now().isoformat(),
        })

    def count_by_type(self) -> dict[str, int]:
        counter = Counter(e["type"] for e in self._events)
        return dict(counter)

    def recent(self, limit: int = 10) -> list[dict]:
        return self._events[-limit:]

    def filter_by_type(self, event_type: str) -> list[dict]:
        return [e for e in self._events if e["type"] == event_type]

    def total_events(self) -> int:
        return len(self._events)

    def clear(self) -> int:
        count = len(self._events)
        self._events.clear()
        return count
