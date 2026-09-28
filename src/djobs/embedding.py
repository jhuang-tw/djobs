"""Explicit, bounded embedding provider contract. Importing this module is offline.

Providers receive only redacted text, never repositories, tasks or receipts.
A timed-out provider is not retried and cannot cause an unbounded thread queue.
"""

from __future__ import annotations

import hashlib
import json
import math
import queue
import re
import threading
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Literal, Protocol

from djobs.memory_policy import REDACTION_VERSION
from djobs.privacy import redact_text

Purpose = Literal["query", "passage"]


@dataclass(frozen=True, slots=True)
class EmbeddingIdentity:
    provider_id: str
    model_id: str
    model_revision: str
    dimension: int
    redaction_version: str = REDACTION_VERSION

    def __post_init__(self) -> None:
        for value in (self.provider_id, self.model_id, self.model_revision):
            if (
                not isinstance(value, str)
                or not re.fullmatch(r"[A-Za-z0-9_./:@+\-]{1,256}", value)
                or redact_text(value) != value
            ):
                raise ValueError("invalid or sensitive embedding identity")
        if isinstance(self.dimension, bool) or not isinstance(self.dimension, int):
            raise ValueError("embedding dimension must be an integer")
        if not 1 <= self.dimension <= 4096 or self.redaction_version != REDACTION_VERSION:
            raise ValueError("unsupported embedding identity")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


class EmbeddingProvider(Protocol):
    @property
    def identity(self) -> EmbeddingIdentity: ...

    def embed(self, texts: Sequence[str], *, purpose: Purpose) -> Sequence[Sequence[float]]: ...


class ProviderUnavailableError(RuntimeError):
    """Only this bounded, non-sensitive code may cross the provider boundary."""


class UnavailableEmbeddingProvider:
    """Explicit failed initialization, not a silently disabled semantic configuration."""

    identity = EmbeddingIdentity("unavailable", "not-loaded", "1", 1)

    def embed(self, texts: Sequence[str], *, purpose: Purpose) -> Sequence[Sequence[float]]:
        raise ProviderUnavailableError("provider_initialization_failed")


def normalize_vector(values: Sequence[float], dimension: int) -> tuple[float, ...]:
    if len(values) != dimension or any(isinstance(value, bool) for value in values):
        raise ValueError("invalid embedding dimension")
    vector = tuple(float(value) for value in values)
    if not all(math.isfinite(value) for value in vector):
        raise ValueError("non-finite embedding")
    norm = math.sqrt(sum(value * value for value in vector))
    if not math.isfinite(norm) or norm <= 1e-12:
        raise ValueError("invalid embedding norm")
    return tuple(value / norm for value in vector)


@dataclass(slots=True)
class EmbeddingSession:
    """One opt-in provider with a single outstanding call and an actual deadline.

    The daemon only computes embeddings. It has no callback capable of changing
    the database after the caller has timed out. Maintenance uses explicit larger
    batch deadlines; ordinary reads use the short configured query deadline.
    """

    provider: EmbeddingProvider
    query_timeout_seconds: float = 0.5
    calls: int = field(default=0, init=False)
    failures: int = field(default=0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _identity: EmbeddingIdentity = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.query_timeout_seconds)
            or not 0.005 <= self.query_timeout_seconds <= 2
        ):
            raise ValueError("query timeout must be between 0.005 and 2 seconds")
        self._identity = self.provider.identity
        if not isinstance(self._identity, EmbeddingIdentity):
            raise ValueError("provider must declare an embedding identity")

    @property
    def identity(self) -> EmbeddingIdentity:
        return self._identity

    def embed(
        self, texts: Sequence[str], *, purpose: Purpose, maintenance: bool = False
    ) -> tuple[tuple[float, ...], ...]:
        if purpose not in {"query", "passage"} or not 1 <= len(texts) <= 16:
            raise ValueError("invalid bounded embedding batch")
        if self.provider.identity != self._identity:
            raise ProviderUnavailableError("provider_identity_changed")
        safe = tuple(redact_text(text)[:2000] for text in texts)
        if not self._lock.acquire(blocking=False):
            self.failures += 1
            raise ProviderUnavailableError("provider_busy_after_timeout")
        result: queue.Queue[tuple[tuple[float, ...], ...] | None] = queue.Queue(maxsize=1)
        self.calls += 1

        def invoke() -> None:
            try:
                values = self.provider.embed(safe, purpose=purpose)
                if len(values) != len(safe):
                    raise ValueError("invalid embedding count")
                normalized = tuple(
                    normalize_vector(value, self.identity.dimension) for value in values
                )
                result.put_nowait(normalized)
            except Exception:
                # Provider exception text/response bodies can contain credentials.
                result.put_nowait(None)
            finally:
                self._lock.release()

        worker = threading.Thread(target=invoke, name="djobs-embedding", daemon=True)
        try:
            worker.start()
        except Exception:
            self._lock.release()
            self.failures += 1
            raise ProviderUnavailableError("provider_unavailable") from None
        try:
            value = result.get(timeout=10.0 if maintenance else self.query_timeout_seconds)
        except queue.Empty:
            self.failures += 1
            raise ProviderUnavailableError("provider_timeout") from None
        if value is None:
            self.failures += 1
            raise ProviderUnavailableError("provider_unavailable")
        return value
