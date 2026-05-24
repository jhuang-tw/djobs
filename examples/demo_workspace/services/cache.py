"""In-memory TTL cache with automatic expiration cleanup."""

import time

class CacheEntry:
    def __init__(self, value, ttl_seconds: int):
        self.value = value
        self.expires_at = time.time() + ttl_seconds

    def is_expired(self) -> bool:
        return time.time() > self.expires_at

class SimpleCache:
    def __init__(self, default_ttl: int = 300):
        self._store: dict[str, CacheEntry] = {}
        self.default_ttl = default_ttl

    def get(self, key: str):
        entry = self._store.get(key)
        if entry is None or entry.is_expired():
            return None
        return entry.value

    def set(self, key: str, value, ttl: int | None = None) -> None:
        self._store[key] = CacheEntry(value, ttl or self.default_ttl)

    def delete(self, key: str) -> bool:
        return self._store.pop(key, None) is not None

    def clear_expired(self) -> int:
        expired = [k for k, v in self._store.items() if v.is_expired()]
        for k in expired:
            del self._store[k]
        return len(expired)
