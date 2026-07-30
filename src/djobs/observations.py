"""Client-neutral repository observations and Git snapshots.

Passive observations are repository-family scoped while explicit durable jobs remain
checkout scoped. Memory rows carry a small lifecycle state so stale or superseded
facts remain auditable without being injected into normal agent context.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from djobs.ranking import rank_memory_rows
from djobs.storage.maintenance import storage_maintenance
from djobs.storage.memory import memory_repository

_MAX_SUMMARY = 500
_MAX_METADATA = 1000
_MAX_CAPSULE_METADATA = 2400
_MAX_OBSERVATIONS_PER_WORKSPACE = 1000
_MAX_CONTEXT_MARKERS_PER_WORKSPACE = 256
_HASH_CHUNK_SIZE = 64 * 1024
_MAX_UNTRACKED_HASH_BYTES = 1024 * 1024
_CONTEXT_INJECTED_EVENT = "context_injected"
_ACTIVE_MEMORY_STATES = {"active"}
MemoryStatus = Literal["active", "resolved", "superseded", "stale", "contradicted"]


def clean(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").replace("\x00", "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _session_hash(agent: Any) -> str:
    return hashlib.sha256(agent.session_id.encode("utf-8")).hexdigest()[:16]


def _memory_scope(workspace: Any) -> str:
    return str(getattr(workspace, "repo_family_id", "") or workspace.workspace_id)


def _memory_ids(workspace: Any) -> tuple[str, ...]:
    values = getattr(workspace, "memory_correlation_ids", ()) or workspace.correlation_ids
    return tuple(dict.fromkeys(str(value) for value in values if str(value)))


def _checkout_scope(workspace: Any) -> str:
    return str(getattr(workspace, "checkout_id", "") or workspace.workspace_id)


def _metadata_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    try:
        decoded = json.loads(str(raw or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _capsule_metadata_json(metadata: dict[str, Any], limit: int) -> str:
    """Serialize a provenance-aware capsule without discarding its schema."""

    def text_list(value: Any, *, item_limit: int, count: int) -> list[str]:
        values = value if isinstance(value, list) else []
        return [clean(item, item_limit) for item in values[-count:] if clean(item, item_limit)]

    def provenance_item(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        item: dict[str, Any] = {}
        for key, bound in (("source", 40), ("evidence_id", 64), ("kind", 40)):
            if value.get(key):
                item[key] = clean(value[key], bound)
        if value.get("advisory") is not None:
            item["advisory"] = bool(value["advisory"])
        return item

    raw_provenance = metadata.get("provenance")
    provenance = raw_provenance if isinstance(raw_provenance, dict) else {}
    capsule: dict[str, Any] = {
        "capsule_schema": 2,
        "memory_status": metadata.get("memory_status", "active"),
        "stored_as_data": True,
        "reason": clean(metadata.get("reason"), 80),
        "goal": clean(metadata.get("goal"), 320),
        "constraints": text_list(metadata.get("constraints"), item_limit=180, count=3),
        "progress": text_list(metadata.get("progress"), item_limit=180, count=4),
        "failures": text_list(metadata.get("failures"), item_limit=180, count=3),
        "next": clean(metadata.get("next"), 220),
        "source_event_ids": text_list(metadata.get("source_event_ids"), item_limit=64, count=12),
        "provenance": {
            "goal": provenance_item(provenance.get("goal")),
            "constraints": [
                item
                for value in (provenance.get("constraints") or [])[-3:]
                if (item := provenance_item(value))
            ],
            "progress": [
                item
                for value in (provenance.get("progress") or [])[-4:]
                if (item := provenance_item(value))
            ],
            "failures": [
                item
                for value in (provenance.get("failures") or [])[-3:]
                if (item := provenance_item(value))
            ],
            "next": provenance_item(provenance.get("next")),
        },
    }
    truncated_fields: set[str] = set()

    def render() -> str:
        payload = dict(capsule)
        if truncated_fields:
            payload["truncated_fields"] = sorted(truncated_fields)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)

    raw = render()
    while len(raw) > limit:
        if capsule["source_event_ids"]:
            capsule["source_event_ids"].pop(0)
            truncated_fields.add("source_event_ids")
        elif capsule["provenance"]["progress"]:
            capsule["provenance"]["progress"].pop(0)
            truncated_fields.add("provenance.progress")
        elif capsule["provenance"]["failures"]:
            capsule["provenance"]["failures"].pop(0)
            truncated_fields.add("provenance.failures")
        elif len(capsule["progress"]) > 1:
            capsule["progress"].pop(0)
            truncated_fields.add("progress")
        elif len(capsule["failures"]) > 1:
            capsule["failures"].pop(0)
            truncated_fields.add("failures")
        elif capsule["constraints"]:
            capsule["constraints"].pop(0)
            truncated_fields.add("constraints")
        else:
            candidates = [
                (len(str(capsule[key])), key)
                for key in ("goal", "next", "reason")
                if len(str(capsule[key])) > 48
            ]
            if not candidates:
                break
            length, key = max(candidates)
            capsule[key] = clean(capsule[key], max(48, int(length * 0.7)))
            truncated_fields.add(key)
        raw = render()

    if len(raw) > limit:
        capsule["constraints"] = []
        capsule["progress"] = capsule["progress"][-1:]
        capsule["failures"] = capsule["failures"][-1:]
        capsule["source_event_ids"] = []
        capsule["provenance"] = {
            "goal": capsule["provenance"]["goal"],
            "constraints": [],
            "progress": [],
            "failures": [],
            "next": capsule["provenance"]["next"],
        }
        truncated_fields.update(
            {"constraints", "progress", "failures", "source_event_ids", "provenance"}
        )
        raw = render()
    return raw


def _metadata_json(metadata: dict[str, Any] | None, *, limit: int | None = None) -> str:
    # Serialize bounded metadata without ever producing invalid JSON.
    resolved_limit = _MAX_METADATA if limit is None else max(200, int(limit))
    normalized = dict(metadata or {})
    normalized.setdefault("memory_status", "active")
    normalized.setdefault("stored_as_data", True)
    if normalized.get("capsule_schema") in {1, 2}:
        return _capsule_metadata_json(normalized, resolved_limit)
    raw = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(raw) <= resolved_limit:
        return raw
    preview_limit = max(80, resolved_limit - 120)
    return json.dumps(
        {
            "truncated": True,
            "preview": clean(raw, preview_limit),
            "memory_status": normalized.get("memory_status", "active"),
            "stored_as_data": True,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _memory_status(raw: Any) -> str:
    status = str(_metadata_dict(raw).get("memory_status") or "active").casefold()
    return (
        status
        if status in {"active", "resolved", "superseded", "stale", "contradicted"}
        else "active"
    )


def ensure_schema(repo: Any) -> None:
    """Initialize passive-memory storage through the formal adapter."""

    memory_repository(repo).ensure_schema()


def _observation_record(
    workspace: Any,
    agent: Any,
    event_type: str,
    summary: str,
    *,
    tool_name: str | None = None,
    metadata: dict[str, Any] | None = None,
    metadata_limit: int | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    memory_id = uuid.uuid4().hex
    scope_id = _memory_scope(workspace)
    normalized_metadata = dict(metadata or {})
    normalized_metadata.setdefault("checkout_id", _checkout_scope(workspace))
    normalized_metadata.setdefault("repo_family_id", scope_id)
    return {
        "id": memory_id,
        "correlation_id": scope_id,
        "agent_type": agent.agent_type,
        "session_id_hash": _session_hash(agent),
        "event_type": clean(event_type, 80),
        "tool_name": clean(tool_name, 80) or None,
        "summary": clean(summary, _MAX_SUMMARY),
        "metadata_json": _metadata_json(normalized_metadata, limit=metadata_limit),
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
    }


def _insert_observation(
    repo: Any,
    workspace: Any,
    agent: Any,
    event_type: str,
    summary: str,
    *,
    tool_name: str | None = None,
    metadata: dict[str, Any] | None = None,
    metadata_limit: int | None = None,
    created_at: str | None = None,
) -> str:
    record = _observation_record(
        workspace,
        agent,
        event_type,
        summary,
        tool_name=tool_name,
        metadata=metadata,
        metadata_limit=metadata_limit,
        created_at=created_at,
    )
    return memory_repository(repo).insert_observation(
        record,
        marker_event=_CONTEXT_INJECTED_EVENT,
        max_observations=_MAX_OBSERVATIONS_PER_WORKSPACE,
        max_markers=_MAX_CONTEXT_MARKERS_PER_WORKSPACE,
    )


def record_observation(
    repo: Any,
    workspace: Any,
    agent: Any,
    event_type: str,
    summary: str,
    *,
    tool_name: str | None = None,
    metadata: dict[str, Any] | None = None,
    metadata_limit: int | None = None,
) -> None:
    _insert_observation(
        repo,
        workspace,
        agent,
        event_type,
        summary,
        tool_name=tool_name,
        metadata=metadata,
        metadata_limit=metadata_limit,
    )


def record_unique_session_observation(
    repo: Any,
    workspace: Any,
    agent: Any,
    event_type: str,
    summary: str,
    *,
    tool_name: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Insert one exact observation at most once per client session."""

    record = _observation_record(
        workspace,
        agent,
        event_type,
        summary,
        tool_name=tool_name,
        metadata=metadata,
    )
    return memory_repository(repo).insert_unique_observation(
        record,
        scopes=_memory_ids(workspace),
        marker_event=_CONTEXT_INJECTED_EVENT,
        max_observations=_MAX_OBSERVATIONS_PER_WORKSPACE,
        max_markers=_MAX_CONTEXT_MARKERS_PER_WORKSPACE,
    )


def reset_context_injection(repo: Any, workspace: Any, agent: Any) -> None:
    memory_repository(repo).reset_context_marker(
        scope=_memory_scope(workspace),
        agent_type=agent.agent_type,
        session_hash=_session_hash(agent),
        marker_event=_CONTEXT_INJECTED_EVENT,
    )


def claim_context_injection(
    repo: Any,
    workspace: Any,
    agent: Any,
    *,
    context_key: str | None = None,
) -> bool:
    """Atomically deduplicate repository-context injection per distinct request."""

    key_hash = (
        hashlib.sha256(clean(context_key, 500).encode("utf-8")).hexdigest()[:16]
        if context_key
        else "session"
    )
    marker_summary = f"Read-only repository context injected ({key_hash})."
    record = _observation_record(
        workspace,
        agent,
        _CONTEXT_INJECTED_EVENT,
        marker_summary,
        metadata={"internal": True, "context_key_hash": key_hash},
    )
    return memory_repository(repo).claim_context_marker(
        record,
        marker_event=_CONTEXT_INJECTED_EVENT,
        max_observations=_MAX_OBSERVATIONS_PER_WORKSPACE,
        max_markers=_MAX_CONTEXT_MARKERS_PER_WORKSPACE,
    )


def _row_to_observation(
    row: Any,
    *,
    score: float | None = None,
    matched_by: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    metadata = _metadata_dict(row["metadata_json"])
    item: dict[str, Any] = {
        "id": row["id"],
        "agent": row["agent_type"],
        "event": row["event_type"],
        "tool": row["tool_name"],
        "summary": row["summary"],
        "created_at": row["created_at"],
        "status": _memory_status(metadata),
    }
    for field in (
        "checkout_id",
        "commit_sha",
        "branch",
        "affected_files",
        "superseded_by",
        "resolved_by_commit",
    ):
        if metadata.get(field) not in (None, "", []):
            item[field] = metadata[field]
    if row["event_type"] == "session_capsule":
        for field in ("goal", "constraints", "progress", "failures", "next"):
            if metadata.get(field) not in (None, "", []):
                item[field] = metadata[field]
    if score is not None:
        item["score"] = round(float(score), 4)
    if matched_by:
        item["matched_by"] = list(matched_by)
    if metadata.get("provenance"):
        item["provenance"] = metadata["provenance"]
    return item


def _valid_rows(repo: Any, rows: list[Any], *, component: str) -> list[Any]:
    valid: list[Any] = []
    for row in rows:
        raw = row["metadata_json"]
        if isinstance(raw, dict):
            valid.append(row)
            continue
        try:
            decoded = json.loads(str(raw or "{}"))
        except (TypeError, json.JSONDecodeError):
            decoded = None
        if isinstance(decoded, dict):
            valid.append(row)
            continue
        from djobs.diagnostics import record_failure

        record_failure(
            repo,
            component,
            ValueError("invalid passive-memory metadata JSON"),
            context={
                "memory_id": str(row["id"]),
                "event_type": str(row["event_type"]),
            },
        )
    return valid


def _active_rows(rows: list[Any]) -> list[Any]:
    return [row for row in rows if _memory_status(row["metadata_json"]) in _ACTIVE_MEMORY_STATES]


def recent_observations(repo: Any, workspace: Any, limit: int = 6) -> list[dict[str, Any]]:
    capped = max(1, min(limit, 20))
    rows = memory_repository(repo).recent_rows(
        scopes=_memory_ids(workspace),
        marker_event=_CONTEXT_INJECTED_EVENT,
        limit=capped * 5,
    )
    valid = _valid_rows(repo, rows, component="memory.recent.corrupt_row")
    return [_row_to_observation(row) for row in _active_rows(valid)[:capped]]


def _search_terms(query: str) -> list[str]:
    terms = [item.strip("._-/") for item in re.findall(r"[\w./-]+", query, re.UNICODE)]
    return [item for item in terms if len(item) >= 2][:16]


def _fts_query(query: str) -> str:
    terms = _search_terms(query)
    return " OR ".join(f'"{term.replace(chr(34), "")}"' for term in terms)


def search_observations(
    repo: Any,
    workspace: Any,
    query: str,
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Return active repository memories with deterministic explanations."""

    cleaned_query = clean(query, 500)
    if not cleaned_query:
        return recent_observations(repo, workspace, limit=limit)
    capped = max(1, min(limit, 20))
    adapter = memory_repository(repo)
    scopes = _memory_ids(workspace)
    candidates: dict[str, dict[str, Any]] = {}
    fts = _fts_query(cleaned_query)
    if fts:
        for row in adapter.fts_rows(
            scopes=scopes,
            query=fts,
            marker_event=_CONTEXT_INJECTED_EVENT,
            limit=max(40, capped * 8),
        ):
            candidates[str(row["id"])] = row
    for row in adapter.scan_rows(
        scopes=scopes,
        marker_event=_CONTEXT_INJECTED_EVENT,
        limit=300,
    ):
        candidates.setdefault(str(row["id"]), row)

    valid_candidates = _valid_rows(
        repo,
        list(candidates.values()),
        component="memory.search.corrupt_row",
    )
    ranked = rank_memory_rows(
        valid_candidates,
        query=cleaned_query,
        workspace_root=workspace.root,
        limit=capped,
    )
    return [
        _row_to_observation(
            item.row,
            score=item.score,
            matched_by=item.matched_by,
        )
        for item in ranked
    ]


def memory_context_hash(memory: Any) -> str:
    """Hash only model-relevant memory fields for no-op context recovery."""

    if isinstance(memory, dict):
        payload: Any = memory
    elif isinstance(memory, list):
        payload = sorted(
            (
                {
                    "id": item.get("id"),
                    "status": item.get("status"),
                    "event": item.get("event"),
                    "summary": item.get("summary"),
                    "commit_sha": item.get("commit_sha"),
                    "branch": item.get("branch"),
                }
                for item in memory
                if isinstance(item, dict)
            ),
            key=lambda item: (
                str(item.get("event") or ""),
                str(item.get("summary") or ""),
                str(item.get("id") or ""),
            ),
        )
    else:
        payload = []
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def update_observation_status(
    repo: Any,
    workspace: Any,
    memory_id: str,
    status: MemoryStatus,
    *,
    replacement_id: str | None = None,
    resolved_by_commit: str | None = None,
) -> bool:
    """Mark a memory inactive while keeping it available for audit."""

    if status not in {"active", "resolved", "superseded", "stale", "contradicted"}:
        raise ValueError(f"unsupported memory status: {status}")
    adapter = memory_repository(repo)
    raw = adapter.observation_metadata(memory_id=memory_id, scopes=_memory_ids(workspace))
    if raw is None:
        return False
    metadata = _metadata_dict(raw)
    metadata["memory_status"] = status
    metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
    if replacement_id:
        metadata["superseded_by"] = replacement_id
    if resolved_by_commit:
        metadata["resolved_by_commit"] = clean(resolved_by_commit, 64)
    return adapter.update_observation_metadata(
        memory_id=memory_id,
        metadata_json=_metadata_json(metadata),
    )


def session_observations(repo: Any, workspace: Any, agent: Any, limit: int = 40) -> list[Any]:
    return memory_repository(repo).session_rows(
        scopes=_memory_ids(workspace),
        session_hash=_session_hash(agent),
        limit=max(1, min(limit, 100)),
    )


def record_session_capsule(
    repo: Any,
    workspace: Any,
    agent: Any,
    *,
    reason: str,
    next_hint: str | None = None,
) -> bool:
    rows = session_observations(repo, workspace, agent)
    if not rows:
        return False
    intents = [
        (str(row["id"]), clean(row["summary"], 320))
        for row in rows
        if row["event_type"] == "user_intent"
    ]
    progress_rows = [
        row for row in rows if row["event_type"] in {"tool_result", "repository_change"}
    ]
    failure_rows = [row for row in rows if row["event_type"] == "tool_failure"]
    goal_id, goal = (
        intents[-1] if intents else ("", "Continue the repository work recorded in this session.")
    )
    constraints = [summary for _memory_id, summary in intents[:-1]][-3:]
    progress = [clean(row["summary"], 220) for row in progress_rows]
    failures = [clean(row["summary"], 220) for row in failure_rows]
    parts = [f"Goal: {goal}"]
    if constraints:
        parts.append("Constraints: " + " | ".join(constraints))
    if progress:
        parts.append("Progress: " + " | ".join(progress[-3:]))
    if failures:
        parts.append("Failed: " + " | ".join(failures[-2:]))
    if next_hint:
        parts.append("Next: " + clean(next_hint, 240))
    record_observation(
        repo,
        workspace,
        agent,
        "session_capsule",
        clean(" || ".join(parts), _MAX_SUMMARY),
        metadata={
            "capsule_schema": 2,
            "reason": clean(reason, 80),
            "goal": goal,
            "constraints": constraints,
            "progress": progress[-5:],
            "failures": failures[-3:],
            "next": clean(next_hint, 240) if next_hint else None,
            "source_event_ids": [str(row["id"]) for row in rows[-20:]],
            "source": "agent_summary",
            "provenance": {
                "goal": {"source": "user_intent", "evidence_id": goal_id},
                "constraints": [
                    {"source": "user_intent", "evidence_id": memory_id}
                    for memory_id, _summary in intents[:-1][-3:]
                ],
                "progress": [
                    {
                        "source": str(row["event_type"]),
                        "evidence_id": str(row["id"]),
                    }
                    for row in progress_rows[-5:]
                ],
                "failures": [
                    {"source": "tool_failure", "evidence_id": str(row["id"])}
                    for row in failure_rows[-3:]
                ],
                "next": {
                    "source": "agent_summary",
                    "kind": "derived_next_step",
                    "advisory": True,
                },
            },
        },
        metadata_limit=_MAX_CAPSULE_METADATA,
    )
    return True


def forget_observation(repo: Any, workspace: Any, memory_id: str) -> bool:
    return memory_repository(repo).forget(
        memory_id=memory_id,
        scopes=_memory_ids(workspace),
    )


def clear_workspace_memory(repo: Any, workspace: Any) -> int:
    return memory_repository(repo).clear(
        scopes=_memory_ids(workspace),
        checkout_id=_checkout_scope(workspace),
    )


def workspace_memory_stats(repo: Any, workspace: Any) -> dict[str, Any]:
    """Return bounded retention statistics without reading explicit tasks."""

    raw = memory_repository(repo).stats(scopes=_memory_ids(workspace))
    by_event: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for row in raw.get("rows", []):
        count = int(row.get("count", 0))
        event = str(row.get("event_type") or "unknown")
        status = _memory_status(row.get("metadata_json"))
        by_event[event] = by_event.get(event, 0) + count
        by_status[status] = by_status.get(status, 0) + count
    return {
        "total": int(raw.get("total", 0)),
        "by_event": dict(sorted(by_event.items())),
        "by_status": dict(sorted(by_status.items())),
        "explicit_tasks_included": False,
    }


def compact_workspace_memory(
    repo: Any,
    workspace: Any,
    *,
    keep_recent: int = 100,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Remove bounded duplicate/inactive passive memory only."""

    maintenance = storage_maintenance(repo)
    integrity = maintenance.integrity_check()
    if not integrity.get("ok"):
        raise RuntimeError("storage integrity check failed before memory compaction")
    backup = None if dry_run else maintenance.backup()
    if backup is not None and not backup.get("ok"):
        raise RuntimeError(
            f"storage backup is required before compaction: {backup.get('reason', 'unknown')}"
        )
    result = memory_repository(repo).compact(
        scopes=_memory_ids(workspace),
        keep_recent=max(1, min(int(keep_recent), 10_000)),
        dry_run=bool(dry_run),
    )
    return {
        **result,
        "dry_run": bool(dry_run),
        "integrity": integrity,
        "backup": backup,
        "explicit_tasks_preserved": True,
    }


def _hash_command(digest: Any, command: list[str]) -> bool:
    try:
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(
                command,
                stdout=output,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
            if result.returncode != 0:
                return False
            output.seek(0)
            while chunk := output.read(_HASH_CHUNK_SIZE):
                digest.update(chunk)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def _hash_file(digest: Any, path: Path) -> None:
    try:
        info = path.lstat()
    except OSError:
        digest.update(b"missing")
        return
    digest.update(f"{info.st_mode}:{info.st_size}:{info.st_mtime_ns}".encode())
    if stat.S_ISLNK(info.st_mode):
        try:
            digest.update(os.readlink(path).encode("utf-8", errors="surrogateescape"))
        except OSError:
            digest.update(b"broken-symlink")
        return
    if not stat.S_ISREG(info.st_mode):
        return
    try:
        with path.open("rb") as stream:
            if info.st_size <= _MAX_UNTRACKED_HASH_BYTES:
                while chunk := stream.read(_HASH_CHUNK_SIZE):
                    digest.update(chunk)
                return
            digest.update(stream.read(_HASH_CHUNK_SIZE))
            stream.seek(max(0, info.st_size - _HASH_CHUNK_SIZE))
            digest.update(stream.read(_HASH_CHUNK_SIZE))
    except OSError:
        digest.update(b"unreadable")


def _hash_untracked(digest: Any, root: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", root, "ls-files", "--others", "--exclude-standard", "-z"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    root_path = Path(root)
    for raw in sorted(item for item in result.stdout.split(b"\x00") if item):
        digest.update(raw)
        _hash_file(digest, root_path / os.fsdecode(raw))
    return True


def _git_state(root: str) -> tuple[str, str, bool, str, str, list[str]] | None:
    try:
        head = subprocess.run(
            ["git", "-C", root, "rev-parse", "--verify", "HEAD"],
            capture_output=True,
            timeout=3,
            check=False,
        )
        status = subprocess.run(
            ["git", "-C", root, "status", "--porcelain=v1", "--untracked-files=normal"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if status.returncode != 0:
        return None
    head_bytes = head.stdout.strip() if head.returncode == 0 else b"unborn"
    head_text = os.fsdecode(head_bytes)
    status_text = status.stdout.decode("utf-8", errors="replace").strip()
    digest = hashlib.sha256()
    digest.update(head_bytes)
    digest.update(b"\x00")
    digest.update(status.stdout)
    lines = [line for line in status_text.splitlines() if line.strip()]
    if lines:
        tracked_ok = _hash_command(
            digest,
            [
                "git",
                "-C",
                root,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--binary",
                "--no-color",
            ],
        )
        staged_ok = _hash_command(
            digest,
            [
                "git",
                "-C",
                root,
                "diff",
                "--cached",
                "--no-ext-diff",
                "--no-textconv",
                "--binary",
                "--no-color",
            ],
        )
        untracked_ok = _hash_untracked(digest, root)
        if not (tracked_ok and staged_ok and untracked_ok):
            return None
    if not lines:
        summary = f"HEAD {head_text[:12]}; working tree clean"
    else:
        shown = [clean(line, 180) for line in lines[:12]]
        extra = len(lines) - len(shown)
        summary = f"HEAD {head_text[:12]}; " + "; ".join(shown)
        if extra:
            summary += f"; +{extra} more"
    branch = ""
    affected_files: list[str] = []
    try:
        branch_result = subprocess.run(
            ["git", "-C", root, "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if branch_result.returncode == 0:
            branch = clean(branch_result.stdout.strip(), 160)
        affected_files = [
            clean(line[3:], 240) for line in lines[:20] if len(line) > 3 and clean(line[3:], 240)
        ]
    except (OSError, subprocess.SubprocessError):
        pass
    return digest.hexdigest(), summary, bool(lines), head_text, branch, affected_files


def capture_repository_snapshot(repo: Any, workspace: Any, agent: Any) -> bool:
    """Record checkout-local Git state and family-scoped change evidence."""

    state = _git_state(workspace.root)
    if state is None:
        return False
    digest, summary, dirty, commit_sha, branch, affected_files = state
    now = datetime.now(timezone.utc).isoformat()
    checkout_id = _checkout_scope(workspace)
    observation = _observation_record(
        workspace,
        agent,
        "repository_change",
        summary,
        metadata={
            "source": "git_snapshot",
            "checkout_id": checkout_id,
            "commit_sha": commit_sha,
            "branch": branch,
            "affected_files": affected_files,
        },
        created_at=now,
    )
    return memory_repository(repo).upsert_snapshot(
        checkout_id=checkout_id,
        digest=digest,
        summary=summary,
        updated_at=now,
        observation=observation,
        record_initial=dirty,
        marker_event=_CONTEXT_INJECTED_EVENT,
        max_observations=_MAX_OBSERVATIONS_PER_WORKSPACE,
        max_markers=_MAX_CONTEXT_MARKERS_PER_WORKSPACE,
    )
