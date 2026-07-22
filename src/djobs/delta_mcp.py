"""Delta-context MCP entry point for durable coding workflows.

This module extends :mod:`djobs.low_token_mcp` with ``resume_delta``.  The tool
uses SQLite's append-only ``context_revisions`` ledger as a stable workspace
revision, returns only task state touched since the caller's last revision, and
exposes a hash of the complete active workspace state. Exact task records remain
in the normal djobs store and are still available through ``check_task``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from djobs.low_token_mcp import (
    _compact_task,
    _dumps,
    _estimate_tokens,
    _get_queue,
    _server,
)
from djobs.mcp_server import (
    _annotate_resume_tasks,
    _correlation_id_variants,
    _job_to_dict,
)

_INCOMPLETE_STATUSES = ("pending", "running", "retry_scheduled")
_COMPLETED_STATUSES = {"succeeded", "archived"}
_FAILED_STATUSES = {"failed", "dead_lettered"}
_MAX_DELTA_EVENTS = 5000


def _canonical_state_hash(tasks: Iterable[dict[str, Any]]) -> str:
    """Return a deterministic SHA-256 for model-relevant active task state."""

    records: list[dict[str, Any]] = []
    for task in sorted(tasks, key=lambda item: str(item.get("id", ""))):
        records.append(
            {
                "id": task.get("id"),
                "type": task.get("type"),
                "status": task.get("status"),
                "payload": task.get("payload") or {},
                "attempt": task.get("attempt", 0),
                "max_attempts": task.get("max_attempts", 0),
                "last_error": task.get("last_error"),
                "run_after": task.get("run_after"),
                "depends_on": task.get("depends_on") or [],
                "resource_key": task.get("resource_key"),
            }
        )
    encoded = json.dumps(
        records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _workspace_events(
    repo: Any,
    correlation_ids: list[str],
    since_revision: int,
) -> tuple[int, list[dict[str, Any]]]:
    """Return workspace head revision and ordered events after ``since_revision``."""

    if not correlation_ids:
        return 0, []
    placeholders = ",".join("?" for _ in correlation_ids)
    with repo._lock:
        head_row = repo._connection.execute(
            f"""
            SELECT COALESCE(MAX(revision), 0) AS revision
            FROM context_revisions
            WHERE correlation_id IN ({placeholders})
            """,
            tuple(correlation_ids),
        ).fetchone()
        head_revision = int(head_row["revision"] if head_row else 0)
        rows = repo._connection.execute(
            f"""
            SELECT
                revision, job_id, task_type, event_type, status, payload_json,
                attempt, max_attempts, last_error, run_after, depends_on_json,
                resource_key
            FROM context_revisions
            WHERE correlation_id IN ({placeholders})
              AND revision > ?
            ORDER BY revision ASC
            LIMIT ?
            """,
            (*correlation_ids, since_revision, _MAX_DELTA_EVENTS),
        ).fetchall()
    return head_revision, [dict(row) for row in rows]


def _change_kind(task: dict[str, Any], event_types: set[str]) -> str:
    status = str(task.get("status", ""))
    if status in _COMPLETED_STATUSES:
        return "completed"
    if status in _FAILED_STATUSES:
        return "failed"
    if event_types.intersection({"job_created", "state_backfilled"}):
        return "added"
    return "updated"


def _decode_json(value: Any, fallback: Any) -> Any:
    if not isinstance(value, str) or not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _change_item(change: dict[str, Any]) -> dict[str, Any]:
    if change["status"] == "deleted":
        item: dict[str, Any] = {
            "id": change["job_id"],
            "type": change["task_type"],
            "status": "deleted",
        }
        kind = "deleted"
    else:
        task = {
            "id": change["job_id"],
            "type": change["task_type"],
            "status": change["status"],
            "payload": _decode_json(change["payload_json"], {}),
            "attempt": change["attempt"],
            "max_attempts": change["max_attempts"],
            "last_error": change["last_error"],
            "run_after": change["run_after"],
            "depends_on": _decode_json(change["depends_on_json"], []),
            "resource_key": change["resource_key"],
        }
        item = _compact_task(task)
        kind = _change_kind(task, change["event_types"])
    item["change"] = kind
    item["last_event"] = change["last_event"]
    item["event_count"] = change["event_count"]
    item["last_revision"] = change["last_revision"]
    return item


def _build_response(
    *,
    correlation_id: str,
    since_revision: int,
    revision: int,
    head_revision: int,
    state_hash: str,
    reset_required: bool,
    counts: dict[str, int],
    changes: list[dict[str, Any]],
    next_tasks: list[dict[str, Any]],
    token_budget: int,
    unchanged: bool,
) -> dict[str, Any]:
    return {
        "mode": "resume_delta",
        "correlation_id": correlation_id,
        "delta_from": since_revision,
        "revision": revision,
        "head_revision": head_revision,
        "has_more": revision < head_revision,
        "reset_required": reset_required,
        "state_hash": state_hash,
        "snapshot_consistent": revision == head_revision,
        "unchanged": unchanged,
        "counts": counts,
        "changes": changes,
        "next": next_tasks,
        "budget": {
            "requested_tokens": token_budget,
            "estimated_tokens": token_budget,
            "metered": False,
        },
        "retrieve_full_with": "check_task(task_id) or resume_session(correlation_id)",
    }


@_server.tool()
def resume_delta(
    correlation_id: str,
    since_revision: int = 0,
    max_items: int = 5,
    token_budget: int = 600,
    known_state_hash: str | None = None,
    include_blocked: bool = False,
) -> str:
    """Return only workspace changes since a stable event revision.

    ``revision`` is an opaque SQLite event cursor.  Persist it together with
    ``state_hash`` and pass both on the next call.  If no work state changed and
    the supplied hash still matches, the response contains no repeated tasks.
    When ``has_more`` is true, call again with the returned ``revision``.
    """

    if isinstance(since_revision, bool) or not isinstance(since_revision, int):
        return _dumps({"error": "since_revision must be a non-negative integer"})
    if since_revision < 0:
        return _dumps({"error": "since_revision must be a non-negative integer"})
    if isinstance(max_items, bool) or not isinstance(max_items, int):
        return _dumps({"error": "max_items must be a positive integer"})
    if isinstance(token_budget, bool) or not isinstance(token_budget, int):
        return _dumps({"error": "token_budget must be a positive integer"})

    max_items = min(max(1, max_items), 50)
    token_budget = min(max(128, token_budget), 8000)

    queue = _get_queue()
    repo: Any = queue._repository
    if not all(
        hasattr(repo, name) for name in ("_connection", "_lock", "list_jobs_by_correlation_ids")
    ):
        return _dumps({"error": "resume_delta requires SQLite backend"})

    correlation_ids = _correlation_id_variants(correlation_id)
    with repo._lock:
        began_snapshot = not repo._connection.in_transaction
        if began_snapshot:
            repo._connection.execute("BEGIN")
        try:
            all_jobs = repo.list_jobs_by_correlation_ids(correlation_ids)
            head_revision, events = _workspace_events(repo, correlation_ids, since_revision)
            reset_required = since_revision > head_revision
            effective_since = 0 if reset_required else since_revision
            if reset_required:
                head_revision, events = _workspace_events(repo, correlation_ids, effective_since)
        finally:
            if began_snapshot:
                repo._connection.rollback()

    all_tasks = [_job_to_dict(job) for job in all_jobs]
    active_tasks = [task for task in all_tasks if task.get("status") in _INCOMPLETE_STATUSES]
    _annotate_resume_tasks(active_tasks)
    ready_tasks = [task for task in active_tasks if not task.get("blocked_by")]
    blocked_tasks = [task for task in active_tasks if task.get("blocked_by")]
    candidates = ready_tasks + blocked_tasks if include_blocked else ready_tasks
    state_hash = _canonical_state_hash(active_tasks)

    counts = {
        "incomplete": len(active_tasks),
        "ready": len(ready_tasks),
        "blocked": len(blocked_tasks),
        "stale": sum(1 for task in active_tasks if task.get("stale")),
    }

    accepted_changes: dict[str, dict[str, Any]] = {}
    accepted_revision = effective_since

    def materialize_changes(source: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        return [_change_item(change) for change in source.values()]

    for event in events:
        task_id = str(event["job_id"])
        is_new_task = task_id not in accepted_changes
        if is_new_task and len(accepted_changes) >= max_items:
            break

        trial_changes = {
            key: {
                **value,
                "event_types": set(value["event_types"]),
            }
            for key, value in accepted_changes.items()
        }
        change = trial_changes.setdefault(
            task_id,
            {
                "job_id": task_id,
                "task_type": event["task_type"],
                "event_count": 0,
                "last_event": event["event_type"],
                "last_revision": int(event["revision"]),
                "event_types": set(),
            },
        )
        change["event_count"] += 1
        change["last_event"] = event["event_type"]
        change["last_revision"] = int(event["revision"])
        change["event_types"].add(event["event_type"])
        for key in (
            "status",
            "payload_json",
            "attempt",
            "max_attempts",
            "last_error",
            "run_after",
            "depends_on_json",
            "resource_key",
        ):
            change[key] = event[key]

        trial_items = materialize_changes(trial_changes)
        trial = _build_response(
            correlation_id=correlation_id,
            since_revision=since_revision,
            revision=int(event["revision"]),
            head_revision=head_revision,
            state_hash=state_hash,
            reset_required=reset_required,
            counts=counts,
            changes=trial_items,
            next_tasks=[],
            token_budget=token_budget,
            unchanged=False,
        )
        if _estimate_tokens(trial) > token_budget:
            break
        accepted_changes = trial_changes
        accepted_revision = int(event["revision"])

    change_items = materialize_changes(accepted_changes)
    changed_ids = set(accepted_changes)
    no_unseen_events = accepted_revision >= head_revision
    unchanged = (
        no_unseen_events
        and not change_items
        and known_state_hash is not None
        and known_state_hash == state_hash
    )

    next_items: list[dict[str, Any]] = []
    if not unchanged:
        for task in candidates:
            if len(change_items) + len(next_items) >= max_items:
                break
            if str(task["id"]) in changed_ids:
                continue
            item = _compact_task(task)
            trial = _build_response(
                correlation_id=correlation_id,
                since_revision=since_revision,
                revision=accepted_revision if events else head_revision,
                head_revision=head_revision,
                state_hash=state_hash,
                reset_required=reset_required,
                counts=counts,
                changes=change_items,
                next_tasks=[*next_items, item],
                token_budget=token_budget,
                unchanged=False,
            )
            if _estimate_tokens(trial) > token_budget:
                break
            next_items.append(item)

    if not events:
        accepted_revision = head_revision
    result = _build_response(
        correlation_id=correlation_id,
        since_revision=since_revision,
        revision=accepted_revision,
        head_revision=head_revision,
        state_hash=state_hash,
        reset_required=reset_required,
        counts=counts,
        changes=change_items,
        next_tasks=next_items,
        token_budget=token_budget,
        unchanged=unchanged,
    )
    estimate = _estimate_tokens(result)
    if estimate > token_budget and not change_items and not next_items:
        result = {
            "mode": "resume_delta",
            "revision": accepted_revision,
            "head_revision": head_revision,
            "has_more": accepted_revision < head_revision,
            "reset_required": reset_required,
            "state_hash": state_hash,
            "unchanged": unchanged,
            "budget": {
                "requested_tokens": token_budget,
                "estimated_tokens": 0,
                "metered": False,
                "exhausted": True,
            },
        }
        estimate = _estimate_tokens(result)
    result["budget"]["estimated_tokens"] = estimate
    if events and accepted_revision == effective_since:
        result["budget"]["exhausted"] = True
    if len(events) >= _MAX_DELTA_EVENTS and accepted_revision < head_revision:
        result["event_scan_limited"] = True
    return _dumps(result)


def main() -> None:
    """Run djobs with capsule, batch, and delta-context tools registered."""

    _get_queue()
    _server.run(transport="stdio")


if __name__ == "__main__":
    main()
