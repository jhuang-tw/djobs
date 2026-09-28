"""Small typed content contract for source-bound native memory artifacts.

Content hashes exclude mutable lifecycle fields. No type or authority supplied
in a document can activate it; application review owns that transition.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from djobs.privacy import redact_text, redact_value

ArtifactKind = Literal["episode", "fact", "experience", "lesson", "skill_candidate", "skill"]
ArtifactScope = Literal["repository_family", "checkout", "agent", "session"]
ARTIFACT_KINDS = frozenset({"episode", "fact", "experience", "lesson", "skill_candidate", "skill"})
ARTIFACT_SCOPES = frozenset({"repository_family", "checkout", "agent", "session"})
RELATION_KINDS = frozenset(
    {
        "derived_from",
        "supports",
        "contradicts",
        "supersedes",
        "applies_to",
        "learned_from",
        "verifies",
        "invalidates",
    }
)
MAX_ARTIFACTS = 256
MAX_SOURCES = 16
MAX_DEPTH = 16


class ArtifactError(ValueError):
    """Only non-sensitive bounded error codes cross the public boundary."""


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=_json_default,
    )


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return timestamp(value)
    raise ArtifactError("unsupported_json_value")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def timestamp(value: str | datetime | None = None) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and len(value) <= 64:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ArtifactError("invalid_timestamp") from None
    else:
        raise ArtifactError("invalid_timestamp")
    if parsed.tzinfo is None:
        raise ArtifactError("timezone_required")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


def safe_text(value: Any, limit: int, *, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit * 4:
        raise ArtifactError("invalid_bounded_text")
    cleaned = redact_text(value).replace("\x00", "").strip()
    if len(cleaned) > limit or (required and not cleaned):
        raise ArtifactError("invalid_bounded_text")
    return cleaned


@dataclass(frozen=True, slots=True)
class MemorySource:
    kind: Literal["observation", "artifact"]
    id: str

    def __post_init__(self) -> None:
        if self.kind not in {"observation", "artifact"}:
            raise ArtifactError("invalid_source_kind")
        if not isinstance(self.id, str) or not self.id or len(self.id) > 200:
            raise ArtifactError("invalid_source_id")
        if any(char.isspace() or ord(char) < 32 for char in self.id):
            raise ArtifactError("invalid_source_id")

    @classmethod
    def parse(cls, value: Any) -> MemorySource:
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls("artifact" if value.startswith("mem_") else "observation", value)
        if isinstance(value, dict) and set(value) == {"kind", "id"}:
            return cls(value["kind"], value["id"])
        raise ArtifactError("invalid_source_reference")


@dataclass(frozen=True, slots=True)
class ArtifactDraft:
    kind: ArtifactKind
    title: str
    abstract: str
    sources: tuple[MemorySource, ...]
    overview: str = ""
    details: str = "{}"
    scope: ArtifactScope = "repository_family"
    observed_at: str | None = None
    valid_from: str | None = None

    @classmethod
    def parse(cls, payload: Any) -> ArtifactDraft:
        if not isinstance(payload, dict):
            raise ArtifactError("artifact_object_required")
        allowed = {
            "kind",
            "title",
            "abstract",
            "overview",
            "details",
            "sources",
            "scope",
            "observed_at",
            "valid_from",
        }
        if set(payload) - allowed:
            raise ArtifactError("unknown_artifact_fields")
        kind, scope = payload.get("kind"), payload.get("scope", "repository_family")
        if kind not in ARTIFACT_KINDS or scope not in ARTIFACT_SCOPES:
            raise ArtifactError("invalid_artifact_type_or_scope")
        raw_sources = payload.get("sources")
        if not isinstance(raw_sources, (list, tuple)) or not 1 <= len(raw_sources) <= MAX_SOURCES:
            raise ArtifactError("bounded_sources_required")
        sources = tuple(MemorySource.parse(value) for value in raw_sources)
        if len(set(sources)) != len(sources):
            raise ArtifactError("duplicate_source_reference")
        details = payload.get("details", {})
        if not isinstance(details, dict) or len(canonical_json(details)) > 16000:
            raise ArtifactError("invalid_bounded_details")
        return cls(
            kind=kind,
            scope=scope,
            sources=sources,
            title=safe_text(payload.get("title", ""), 160, required=True),
            abstract=safe_text(payload.get("abstract", ""), 500, required=True),
            overview=safe_text(payload.get("overview", ""), 2000),
            details=canonical_json(redact_value(details)),
            observed_at=timestamp(payload["observed_at"]) if payload.get("observed_at") else None,
            valid_from=timestamp(payload["valid_from"]) if payload.get("valid_from") else None,
        )


def observation_hash(row: dict[str, Any]) -> str:
    """Pin immutable evidence, not its independently mutable lifecycle metadata."""
    return digest(
        {
            "id": str(row["id"]),
            "correlation_id": row["correlation_id"],
            "summary": row["summary"],
            "event_type": row["event_type"],
            "tool_name": row.get("tool_name"),
            "created_at": timestamp(row["created_at"]),
            "agent_type": row.get("agent_type"),
            "session_id_hash": row.get("session_id_hash"),
        }
    )


def artifact_content_hash(row: dict[str, Any], sources: list[dict[str, Any]]) -> str:
    content = {
        key: row.get(key)
        for key in (
            "repo_family_id",
            "scope",
            "scope_key",
            "kind",
            "title",
            "abstract",
            "overview",
            "details_json",
            "observed_at",
            "valid_from",
            "schema_version",
            "source_count",
            "proposal_hash",
        )
    }
    content["sources"] = sorted(
        (source["source_kind"], source["source_id"], source["source_hash"]) for source in sources
    )
    return digest(content)
