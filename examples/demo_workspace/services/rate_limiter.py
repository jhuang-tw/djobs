"""Sliding-window rate limiter for per-client request throttling."""

import time
from collections import defaultdict

class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, client_id: str) -> bool:
        now = time.time()
        window_start = now - self.window_seconds
        self._requests[client_id] = [
            t for t in self._requests[client_id] if t > window_start
        ]
        if len(self._requests[client_id]) >= self.max_requests:
            return False
        self._requests[client_id].append(now)
        return True

    def remaining(self, client_id: str) -> int:
        now = time.time()
        window_start = now - self.window_seconds
        recent = [t for t in self._requests[client_id] if t > window_start]
        return max(0, self.max_requests - len(recent))

    def reset(self, client_id: str) -> None:
        self._requests.pop(client_id, None)
