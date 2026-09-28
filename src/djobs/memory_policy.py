"""Pure eligibility and coding-entity rules shared by all memory retrieval paths.

These functions have no storage, provider, prompt or task authority. Legacy
observations remain raw evidence; a caller-supplied metadata label cannot turn
an observation into a reviewed fact or an accepted skill.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from djobs.privacy import REDACTION_VERSION, redact_text

POLICY_VERSION = "djobs-memory-eligibility-v1"
CANDIDATE_BOUND = 1000
_LATIN = re.compile(r"[A-Za-z0-9_./:+-]{2,}")
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_PATH = re.compile(r"(?<!\w)(?:[A-Za-z]:)?(?:[\w.-]+[/\\])+[\w.-]+")
_SYMBOL = re.compile(r"\b(?:test_[A-Za-z0-9_]+|[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+)\b")
_ERROR = re.compile(r"\b[A-Z][A-Za-z0-9]*(?:Error|Exception)\b")
_COMMIT = re.compile(r"\b[0-9a-f]{7,40}\b", re.IGNORECASE)


def metadata_object(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return dict(raw)
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def lexical_terms(value: str) -> tuple[str, ...]:
    """Keep historical ASCII tokens and add deterministic CJK bigrams.

    This is lexical matching, not a translation or semantic embedding model.
    Single-character CJK queries remain searchable without matching every row.
    """

    terms = [term.casefold() for term in _LATIN.findall(value)]
    for segment in _CJK.findall(value):
        terms.extend(
            [segment]
            if len(segment) == 1
            else [segment[index : index + 2] for index in range(len(segment) - 1)]
        )
    return tuple(dict.fromkeys(terms))[:128]


def scope_exclusion(
    row: dict[str, Any], workspace: Any, *, session_id_hash: str | None = None
) -> str | None:
    """Scope is enforced for audit as well as recovery; status is a separate axis."""
    metadata = metadata_object(row.get("metadata_json"))
    if metadata is None:
        return "corrupt_metadata"
    if metadata.get("truncated_authority"):
        return "incomplete_authority"
    family = str(getattr(workspace, "repo_family_id", "") or workspace.workspace_id)
    scopes = getattr(workspace, "memory_correlation_ids", ()) or workspace.correlation_ids
    if row.get("correlation_id") and row["correlation_id"] not in scopes:
        return "wrong_repository"
    if metadata.get("repo_family_id") not in (None, "", family):
        return "wrong_repository"
    scope = metadata.get("scope", "repository_family")
    if scope == "checkout":
        checkout = str(getattr(workspace, "checkout_id", "") or workspace.workspace_id)
        if metadata.get("checkout_id") != checkout:
            return "wrong_checkout"
    elif scope == "session" and session_id_hash and row.get("session_id_hash") == session_id_hash:
        return None
    elif scope != "repository_family":
        # Private agent/session/import scopes require an explicit scoped reader;
        # normal family recovery cannot opt into them implicitly.
        return "private_or_unknown_scope"
    return None


def observation_exclusion(
    row: dict[str, Any], workspace: Any, *, now: datetime | None = None
) -> str | None:
    """Return a non-sensitive reason when a row is unsafe for normal recovery."""

    metadata = metadata_object(row.get("metadata_json"))
    if metadata is None:
        return "corrupt_metadata"
    reason = scope_exclusion(row, workspace)
    if reason is not None:
        return reason
    status = str(metadata.get("memory_status", "active")).casefold()
    if status != "active":
        return "inactive_or_unverified"
    if metadata.get("authority", "raw_observation") != "raw_observation":
        return "unreviewed_or_unsupported_derivation"
    if metadata.get("imported_unverified") or metadata.get("import_status"):
        return "imported_unverified"
    if metadata.get("superseded_by") or metadata.get("contradicted_by"):
        return "superseded_or_contradicted"
    current = now or datetime.now(timezone.utc)
    for field, past in (("valid_from", False), ("valid_to", True)):
        raw = metadata.get(field)
        if raw in (None, ""):
            continue
        try:
            instant = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if instant.tzinfo is None:
                return "invalid_validity_window"
            if (past and current >= instant) or (not past and current < instant):
                return "outside_validity_window"
        except (TypeError, ValueError):
            return "invalid_validity_window"
    return None


def coding_entities(text: str, metadata: dict[str, Any] | None = None) -> tuple[str, ...]:
    """Extract only deterministic coding identifiers, never an inferred ontology."""

    safe = redact_text(text)
    result = {"file:" + path.replace("\\", "/").casefold() for path in _PATH.findall(safe)}
    result.update("symbol:" + name.casefold() for name in _SYMBOL.findall(safe))
    result.update("error:" + name.casefold() for name in _ERROR.findall(safe))
    result.update("commit:" + value.casefold() for value in _COMMIT.findall(safe))
    value = metadata or {}
    for field, kind in (
        ("affected_files", "file"),
        ("branch", "branch"),
        ("commit_sha", "commit"),
        ("task_id", "task"),
        ("component", "component"),
        ("dependency", "dependency"),
        ("tool_name", "tool"),
        ("command", "command"),
    ):
        values = value.get(field, [])
        if not isinstance(values, list):
            values = [values]
        for item in values[:32]:
            if isinstance(item, str) and item.strip():
                cleaned = redact_text(item.strip())[:240].replace("\\", "/").casefold()
                if "<redacted>" not in cleaned:
                    result.add(kind + ":" + cleaned)
    return tuple(sorted(result))[:128]


def embedding_text(row: dict[str, Any]) -> str:
    """The provider only sees bounded redacted content, not IDs or gold labels."""

    return redact_text(str(row.get("summary") or ""))[:2000]


def content_hash(row: dict[str, Any]) -> str:
    return hashlib.sha256((REDACTION_VERSION + "\0" + embedding_text(row)).encode()).hexdigest()


def source_hash(row: dict[str, Any]) -> str:
    """CAS fingerprint includes lifecycle and scope as well as immutable text."""

    value = {
        key: row.get(key)
        for key in (
            "id",
            "correlation_id",
            "summary",
            "event_type",
            "tool_name",
            "metadata_json",
            "created_at",
            "agent_type",
            "session_id_hash",
        )
    }
    raw = json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()
