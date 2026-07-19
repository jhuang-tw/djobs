"""Low-overhead MCP entry point for durable coding workflows.

Run with::

    python -m djobs.low_token_mcp

It exposes the normal djobs MCP tools plus three additive tools that reduce model
round trips and context size:

- ``enqueue_batch``: create many durable tasks in one call;
- ``complete_batch``: complete many tasks in one call;
- ``resume_capsule``: return only the next useful tasks under a token budget.

The queue remains the exact source of truth. Compact responses are reversible:
full records stay available through ``check_task`` and ``resume_session``.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from djobs.core.errors import DJobsError
from djobs.core.pause import is_paused
from djobs.mcp_server import (
    _annotate_resume_tasks,
    _correlation_id_variants,
    _default_correlation_id,
    _dumps,
    _get_queue,
    _job_to_dict,
    _server,
    _start_embedded_daemon,
)

_MAX_BATCH_ITEMS = 200
_CJK_RE = re.compile(
    "[\\u3400-\\u4dbf\\u4e00-\\u9fff\\uf900-\\ufaff"
    "\\u3040-\\u30ff\\uac00-\\ud7af]"
)
_USEFUL_KEYS = (
    "file",
    "path",
    "summary",
    "title",
    "name",
    "description",
    "condition",
    "why",
    "command",
)


def _estimate_tokens(value: Any) -> int:
    """Estimate response tokens without pretending to be provider metering.

    Compact JSON is denser than prose, while CJK characters are commonly close
    to one token each. This is intentionally conservative and dependency-free.
    Every public result labels the estimate ``metered: false``.
    """

    text = value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), default=str
    )
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    non_cjk = max(0, len(text) - cjk)
    divisor = 3 if text.lstrip().startswith(("{", "[")) else 4
    return max(1, cjk + math.ceil(non_cjk / divisor))


def _parse_json_array(raw: str, name: str) -> list[Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid {name} JSON: {exc}") from exc
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a JSON array")
    if not value:
        raise ValueError(f"{name} must contain at least one item")
    if len(value) > _MAX_BATCH_ITEMS:
        raise ValueError(f"{name} may contain at most {_MAX_BATCH_ITEMS} items")
    return value


def _compact_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    compact: dict[str, Any] = {}
    changed = False
    for key in _USEFUL_KEYS:
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if len(value) > 240:
                value = value[:239].rstrip() + "…"
                changed = True
        elif not isinstance(value, (int, float, bool)):
            changed = True
            continue
        compact[key] = value
    if not compact:
        for key, value in payload.items():
            if len(compact) >= 3:
                changed = True
                break
            if isinstance(value, str):
                value = value.strip()
                if len(value) > 240:
                    value = value[:239].rstrip() + "…"
                    changed = True
            elif not isinstance(value, (int, float, bool)):
                changed = True
                continue
            compact[str(key)] = value
    return compact, changed or len(compact) < len(payload)


def _compact_task(task: dict[str, Any], *, minimal: bool = False) -> dict[str, Any]:
    payload = task.get("payload")
    compact, truncated = _compact_payload(payload if isinstance(payload, dict) else {})
    result: dict[str, Any] = {
        "id": task["id"],
        "type": task["type"],
        "status": task["status"],
    }
    if compact and not minimal:
        result["payload"] = compact
    if task.get("blocked_by"):
        result["blocked_by_count"] = len(task["blocked_by"])
    if task.get("stale"):
        result["stale"] = True
        if task.get("age_days") is not None and not minimal:
            result["age_days"] = task["age_days"]
    if truncated and not minimal:
        result["view_truncated"] = True
    return result


def _capsule(
    tasks: list[dict[str, Any]],
    correlation_id: str,
    *,
    max_items: int,
    token_budget: int,
    offset: int,
    include_blocked: bool,
) -> dict[str, Any]:
    max_items = min(max(1, max_items), 50)
    token_budget = min(max(128, token_budget), 8000)
    offset = max(0, offset)
    ready = [task for task in tasks if not task.get("blocked_by")]
    blocked = [task for task in tasks if task.get("blocked_by")]
    candidates = ready + blocked if include_blocked else ready
    full_estimate = _estimate_tokens(tasks)
    selected: list[dict[str, Any]] = []

    def build(items: list[dict[str, Any]]) -> dict[str, Any]:
        next_offset = offset + len(items)
        return {
            "mode": "resume_capsule",
            "correlation_id": correlation_id,
            "counts": {
                "incomplete": len(tasks),
                "ready": len(ready),
                "blocked": len(blocked),
                "stale": sum(bool(task.get("stale")) for task in tasks),
            },
            "page": {
                "offset": offset,
                "returned": len(items),
                "next_offset": next_offset if next_offset < len(candidates) else None,
            },
            "budget": {
                "requested_tokens": token_budget,
                "estimated_tokens": 0,
                "full_view_estimated_tokens": full_estimate,
                "metered": False,
            },
            "recoverable": True,
            "retrieve_full_with": "check_task(task_id) or resume_session(correlation_id)",
            "tasks": items,
        }

    for task in candidates[offset : offset + max_items]:
        item = _compact_task(task)
        trial = build([*selected, item])
        if _estimate_tokens(trial) <= token_budget:
            selected.append(item)
            continue
        if not selected:
            selected.append(_compact_task(task, minimal=True))
        break

    result = build(selected)
    estimate = _estimate_tokens(result)
    while len(selected) > 1 and estimate > token_budget:
        selected.pop()
        result = build(selected)
        estimate = _estimate_tokens(result)
    result["budget"]["estimated_tokens"] = estimate
    if full_estimate:
        result["budget"]["estimated_reduction_percent"] = round(
            max(0.0, 1.0 - estimate / full_estimate) * 100, 1
        )
    return result


@_server.tool()
def enqueue_batch(tasks: str, correlation_id: str | None = None) -> str:
    """Create up to 200 tasks in one MCP/model round trip.

    ``tasks`` is a JSON array. Each item accepts ``type``/``task_type``,
    ``payload``, ``max_attempts``, ``idempotency_key``, ``depends_on``, and
    ``resource_key``. The compact response omits bookkeeping fields; full task
    records remain available through existing djobs tools.
    """

    from djobs.mcp_server import _db_path

    if is_paused(_db_path):
        return _dumps({"paused": True, "skipped": True, "message": "djobs is paused"})
    try:
        raw_items = _parse_json_array(tasks, "tasks")
        specs: list[dict[str, Any]] = []
        for index, item in enumerate(raw_items):
            if not isinstance(item, dict):
                raise ValueError(f"tasks[{index}] must be an object")
            task_type = item.get("type", item.get("task_type"))
            if not isinstance(task_type, str) or not task_type.strip():
                raise ValueError(f"tasks[{index}].type must be a non-empty string")
            payload = item.get("payload", {})
            if not isinstance(payload, dict):
                raise ValueError(f"tasks[{index}].payload must be an object")
            max_attempts = item.get("max_attempts", 3)
            if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1:
                raise ValueError(f"tasks[{index}].max_attempts must be a positive integer")
            spec: dict[str, Any] = {
                "type": task_type.strip(),
                "payload": payload,
                "max_attempts": max_attempts,
            }
            for key in ("idempotency_key", "depends_on", "resource_key"):
                if key in item:
                    spec[key] = item[key]
            specs.append(spec)
    except ValueError as exc:
        return _dumps({"error": "invalid batch", "detail": str(exc)})

    correlation_id = correlation_id or _default_correlation_id()
    jobs = _get_queue().submit_batch(specs, correlation_id=correlation_id)
    summaries: list[dict[str, Any]] = []
    for job in jobs:
        payload, _ = _compact_payload(job.payload)
        label = (
            payload.get("file")
            or payload.get("path")
            or payload.get("summary")
            or payload.get("title")
            or job.type
        )
        summaries.append({"id": job.id, "type": job.type, "label": label})
    return _dumps(
        {"accepted_count": len(jobs), "correlation_id": correlation_id, "tasks": summaries}
    )


@_server.tool()
def complete_batch(completions: str) -> str:
    """Complete up to 200 tasks in one MCP/model round trip.

    Items may be task-id strings or ``{"task_id": ..., "evidence": ...}``
    objects. Successful ids are omitted from the response because the caller
    already supplied them; only failures are returned in detail.
    """

    try:
        raw_items = _parse_json_array(completions, "completions")
    except ValueError as exc:
        return _dumps({"error": "invalid batch", "detail": str(exc)})

    queue = _get_queue()
    completed = 0
    failures: list[dict[str, str]] = []
    for index, item in enumerate(raw_items):
        if isinstance(item, str):
            task_id, evidence = item, None
        elif isinstance(item, dict):
            task_id = item.get("task_id", item.get("id"))
            evidence = item.get("evidence")
        else:
            failures.append({"task_id": f"index:{index}", "error": "invalid completion item"})
            continue
        if not isinstance(task_id, str) or not task_id.strip():
            failures.append({"task_id": f"index:{index}", "error": "missing task_id"})
            continue
        if evidence is not None and not isinstance(evidence, str):
            failures.append({"task_id": task_id, "error": "evidence must be a string"})
            continue
        try:
            queue.complete(task_id.strip(), evidence=evidence)
            completed += 1
        except DJobsError as exc:
            failures.append({"task_id": task_id, "error": str(exc)})
    return _dumps(
        {"completed_count": completed, "failed_count": len(failures), "failures": failures}
    )


@_server.tool()
def resume_capsule(
    correlation_id: str,
    max_items: int = 5,
    token_budget: int = 600,
    offset: int = 0,
    include_blocked: bool = False,
) -> str:
    """Return the next useful tasks under an explicit estimated-token budget.

    Ready work is ranked before blocked work. Payloads keep only action-relevant
    fields, responses paginate, and exact originals remain retrievable. Prefer
    this tool for normal recovery; use ``resume_session`` only when full task
    records are actually required.
    """

    queue = _get_queue()
    repo: Any = queue._repository
    if not hasattr(repo, "list_jobs_by_correlation_ids"):
        return _dumps({"error": "resume_capsule requires SQLite backend"})
    jobs = repo.list_jobs_by_correlation_ids(
        _correlation_id_variants(correlation_id),
        ("pending", "running", "retry_scheduled"),
    )
    tasks = [_job_to_dict(job) for job in jobs]
    _annotate_resume_tasks(tasks)
    return _dumps(
        _capsule(
            tasks,
            correlation_id,
            max_items=max_items,
            token_budget=token_budget,
            offset=offset,
            include_blocked=include_blocked,
        )
    )


def main() -> None:
    """Run the normal djobs server with low-token tools registered."""

    _get_queue()
    _start_embedded_daemon()
    _server.run(transport="stdio")


if __name__ == "__main__":
    main()
