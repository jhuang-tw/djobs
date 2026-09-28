"""Opt-in external index comparison without external memory authority.

Only explicitly injected trusted adapter code runs. It receives a bound opaque
namespace and redacted data, never a repository handle, task, review gate or
credential configuration. This is an output/authority contract, not a sandbox
against arbitrary Python code supplied by the embedding application.
"""

from __future__ import annotations

import hashlib
import math
import queue
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from djobs.memory_artifacts import ArtifactError
from djobs.memory_policy import (
    content_hash,
    embedding_text,
    metadata_object,
    observation_exclusion,
)
from djobs.privacy import REDACTION_VERSION, redact_text
from djobs.retrieval import retrieve_memory
from djobs.storage.retrieval import RetrievalIndex


def _family_exportable(row: dict[str, Any], workspace: Any) -> bool:
    """A family namespace never receives checkout/session/agent-private text."""
    metadata = metadata_object(row.get("metadata_json"))
    return (
        metadata is not None
        and metadata.get("scope", "repository_family") == "repository_family"
        and observation_exclusion(row, workspace) is None
    )


class ExternalMemoryAdapter(Protocol):
    adapter_id: str
    revision: str

    def health(self, namespace: str) -> bool: ...

    def index(self, namespace: str, records: Sequence[dict[str, str]]) -> bool: ...

    def retrieve(self, namespace: str, query: str, limit: int) -> Sequence[dict[str, Any]]: ...

    def delete_derived_copy(self, namespace: str, record_ids: Sequence[str]) -> bool: ...


@dataclass(slots=True)
class ExternalMemorySession:
    adapter: ExternalMemoryAdapter
    repository_family: str
    enabled: bool = False
    timeout_seconds: float = 0.5
    calls: int = field(default=0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _identity: tuple[str, str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ArtifactError("explicit_external_enable_flag_required")
        if not math.isfinite(self.timeout_seconds) or not 0.005 <= self.timeout_seconds <= 2:
            raise ArtifactError("external_adapter_timeout_bound")
        values = (self.adapter.adapter_id, self.adapter.revision)
        if any(
            not isinstance(value, str)
            or not re.fullmatch(r"[A-Za-z0-9_.:@/-]{1,120}", value)
            or redact_text(value) != value
            for value in values
        ):
            raise ArtifactError("invalid_external_adapter_identity")
        if not isinstance(self.repository_family, str) or not self.repository_family:
            raise ArtifactError("external_repository_binding_required")
        self._identity = values

    @property
    def namespace(self) -> str:
        value = "\0".join((*self._identity, REDACTION_VERSION, self.repository_family))
        return "djobs-derived-v1-" + hashlib.sha256(value.encode()).hexdigest()

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "adapter": self._identity[0],
            "revision": self._identity[1],
            "namespace": self.namespace,
            "enabled": self.enabled,
            "redaction_version": REDACTION_VERSION,
            "canonical_writer": False,
            "export_scope": "repository_family",
            "default_ranking_changed": False,
            "client_calls": self.calls,
        }

    def _check_workspace(self, workspace: Any) -> None:
        if (workspace.repo_family_id or workspace.workspace_id) != self.repository_family:
            raise ArtifactError("external_repository_mismatch")

    def _call(self, operation: Callable[[], Any]) -> tuple[Any, str | None]:
        if not self.enabled:
            return None, "external_adapter_disabled"
        if (self.adapter.adapter_id, self.adapter.revision) != self._identity:
            return None, "external_adapter_identity_changed"
        if not self._lock.acquire(blocking=False):
            return None, "external_adapter_busy"
        result: queue.Queue = queue.Queue(maxsize=1)
        self.calls += 1

        def invoke():
            try:
                result.put((True, operation()))
            except Exception:
                # Never expose adapter response/exception bodies or credentials.
                result.put((False, None))
            finally:
                self._lock.release()

        worker = threading.Thread(target=invoke, name="djobs-external-index", daemon=True)
        try:
            worker.start()
        except Exception:
            self._lock.release()
            return None, "external_adapter_unavailable"
        try:
            ok, value = result.get(timeout=self.timeout_seconds)
            return (value, None) if ok else (None, "external_adapter_unavailable")
        except queue.Empty:
            return None, "external_adapter_timeout"

    def health(self) -> dict[str, Any]:
        value, reason = self._call(lambda: self.adapter.health(self.namespace))
        return {
            "ok": reason is None and value is True,
            "fallback_reason": reason,
            "continue_coding": True,
            **self.metadata,
        }

    def index(self, repo: Any, workspace: Any, *, confirm: bool = False) -> dict[str, Any]:
        self._check_workspace(workspace)
        if confirm is not True:
            return {"ok": False, "requires_confirmation": True, "changed": False}
        scopes = workspace.memory_correlation_ids or workspace.correlation_ids
        rows = RetrievalIndex(repo).source_rows(tuple(scopes))
        records = tuple(
            {
                "record_id": str(row["id"]),
                "content_hash": content_hash(row),
                "text": embedding_text(row),
            }
            for row in rows
            if _family_exportable(row, workspace)
        )
        value, reason = self._call(lambda: self.adapter.index(self.namespace, records))
        return {
            "ok": reason is None and value is True,
            "fallback_reason": reason,
            "record_count": len(records),
            "canonical_memory_changed": False,
            "external_effect": "unknown" if reason else "adapter_reported",
            "continue_coding": True,
            **self.metadata,
        }

    def retrieve(self, repo: Any, workspace: Any, query: str, *, limit: int = 6) -> dict[str, Any]:
        from djobs.observations import _row_to_observation

        self._check_workspace(workspace)
        cap = max(1, min(int(limit), 20))
        safe_query = redact_text(query)[:500]
        lexical = retrieve_memory(repo, workspace, safe_query, limit=cap)
        started = time.perf_counter()
        values, reason = self._call(lambda: self.adapter.retrieve(self.namespace, safe_query, cap))
        candidates, rejected = [], 0
        if reason is None:
            if not isinstance(values, (list, tuple)) or len(values) > 100:
                reason = "external_response_bound"
            else:
                scopes = workspace.memory_correlation_ids or workspace.correlation_ids
                rows = RetrievalIndex(repo).source_rows(tuple(scopes))
                eligible = {
                    str(row["id"]): row for row in rows if _family_exportable(row, workspace)
                }
                seen = set()
                for value in values:
                    if not isinstance(value, dict) or not isinstance(value.get("record_id"), str):
                        rejected += 1
                        continue
                    identity = value["record_id"]
                    row = eligible.get(identity)
                    if row is None or value.get("content_hash") != content_hash(row):
                        rejected += 1
                        continue
                    if identity in seen:
                        continue
                    seen.add(identity)
                    # Ignore foreign text, authority and probability, even on a valid ID.
                    item = _row_to_observation(row)
                    item["matched_by"] = ["external_id_revalidated"]
                    item["execution_authority"] = False
                    item["external_score_used"] = False
                    candidates.append(item)
                    if len(candidates) >= cap:
                        break
        return {
            "ok": True,
            "memories": lexical.items,
            "external_candidates": candidates,
            "rejected_external_candidates": rejected,
            "external_latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "fallback_reason": reason,
            "continue_coding": True,
            "stored_content_is_data": True,
            "default_ranking_unchanged": True,
            "canonical_memory_changed": False,
            **self.metadata,
        }

    def delete_derived_copy(
        self, record_ids: list[str], *, confirm: bool = False
    ) -> dict[str, Any]:
        if confirm is not True:
            return {"ok": False, "requires_confirmation": True}
        if (
            not isinstance(record_ids, list)
            or not 1 <= len(record_ids) <= 1000
            or any(
                not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value)
                for value in record_ids
            )
        ):
            raise ArtifactError("explicit_bounded_external_ids_required")
        value, reason = self._call(
            lambda: self.adapter.delete_derived_copy(self.namespace, tuple(record_ids))
        )
        return {
            "ok": reason is None and value is True,
            "fallback_reason": reason,
            "external_effect": "unknown" if reason else "adapter_reported",
            "canonical_memory_changed": False,
            "continue_coding": True,
            **self.metadata,
        }
