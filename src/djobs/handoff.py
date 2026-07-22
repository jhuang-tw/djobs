"""High-level, repository-scoped cross-agent checkpoint and handoff tools."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from djobs.mcp_server import configure
from djobs.workspace import (
    AgentSession,
    Workspace,
    resolve_agent_session,
    resolve_workspace,
    shared_db_path,
)

_MAX_STORED_TEXT = 2000
_MAX_LABEL = 320
_DEFAULT_LEASE_SECONDS = 600
_ACTIVE = ("pending", "running", "retry_scheduled")
_FAILED = ("failed", "dead_lettered")


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _estimate_tokens(value: Any) -> int:
    text = value if isinstance(value, str) else _dumps(value)
    return max(1, math.ceil(len(text) / 4)) if text else 0


def _clean_text(value: str | None, limit: int = _MAX_STORED_TEXT) -> str:
    text = " ".join((value or "").replace("\x00", "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _resolve(
    *,
    roots: list[Any] | tuple[Any, ...] | None,
    cwd: str | None,
    agent_type: str | None,
    session_id: str | None,
) -> tuple[Workspace, AgentSession, Any, Any]:
    workspace = resolve_workspace(roots=roots, cwd=cwd)
    agent = resolve_agent_session(
        workspace,
        agent_type=agent_type,
        session_id=session_id,
    )
    queue = configure(str(shared_db_path()))
    repo: Any = queue._repository
    with repo._lock:
        repo._connection.execute("PRAGMA journal_mode = WAL")
        repo._connection.execute("PRAGMA busy_timeout = 5000")
    queue.register_agent(
        agent.agent_id,
        capabilities=["coding", "checkpoint", "handoff"],
        metadata={
            "agent_type": agent.agent_type,
            "session_id": hashlib.sha256(agent.session_id.encode()).hexdigest()[:16],
            "workspace_id": workspace.workspace_id,
            "repository": workspace.root,
        },
    )
    return workspace, agent, queue, repo


def ensure_shared_queue() -> Any:
    """Initialize the shared local database with concurrent-client pragmas."""

    queue = configure(str(shared_db_path()))
    repo: Any = queue._repository
    with repo._lock:
        repo._connection.execute("PRAGMA journal_mode = WAL")
        repo._connection.execute("PRAGMA busy_timeout = 5000")
    return queue


def _scope_sql(workspace: Workspace) -> tuple[str, tuple[str, ...]]:
    placeholders = ",".join("?" for _ in workspace.correlation_ids)
    return placeholders, workspace.correlation_ids


def _recover(queue: Any, repo: Any) -> None:
    try:
        queue.recover_expired_leases()
        queue.reap_stale_agents()
        # Older queue recovery can leave a max-attempt task in retry_scheduled
        # without run_after. Normalize that orphan state so it is claimable again
        # instead of remaining permanently stuck.
        with repo._lock:
            repo._connection.execute(
                "UPDATE jobs SET status = 'pending', updated_at = ? "
                "WHERE status = 'retry_scheduled' AND run_after IS NULL "
                "AND leased_by IS NULL",
                (datetime.now(timezone.utc).isoformat(),),
            )
            repo._connection.commit()
    except Exception:
        # Recovery is best effort. The coding task must remain usable when djobs fails.
        return


def _compact_row(row: Any, current_agent: str) -> dict[str, Any]:
    payload: dict[str, Any]
    try:
        decoded = json.loads(row["payload_json"] or "{}")
        payload = decoded if isinstance(decoded, dict) else {}
    except json.JSONDecodeError:
        payload = {}
    label = _clean_text(
        str(
            payload.get("summary")
            or payload.get("title")
            or payload.get("path")
            or payload.get("file")
            or row["type"]
        ),
        _MAX_LABEL,
    )
    item: dict[str, Any] = {
        "id": row["id"],
        "status": row["status"],
        "summary": label,
    }
    path = payload.get("path") or payload.get("file")
    if isinstance(path, str) and path.strip():
        item["path"] = _clean_text(path, 240)
    leased_by = row["leased_by"]
    if leased_by:
        item["owner"] = "self" if leased_by == current_agent else leased_by.split(":", 1)[0]
        item["lease_expires_at"] = row["lease_expires_at"]
    if row["last_error"]:
        item["error"] = _clean_text(row["last_error"], 240)
    if row["evidence"]:
        item["evidence"] = _clean_text(row["evidence"], 320)
    return item


def _bounded(result: dict[str, Any], token_budget: int) -> str:
    budget = max(64, min(int(token_budget), 4000))
    result["budget"] = {"requested_tokens": budget, "estimated_tokens": 0}
    removable = ("recent_completed", "failed", "other_agents", "tasks")
    while True:
        estimate = _estimate_tokens(result)
        result["budget"]["estimated_tokens"] = estimate
        final_estimate = _estimate_tokens(result)
        if final_estimate <= budget:
            result["budget"]["estimated_tokens"] = final_estimate
            return _dumps(result)
        changed = False
        for key in removable:
            values = result.get(key)
            if isinstance(values, list) and values:
                values.pop()
                changed = True
                break
        if not changed:
            minimal = {
                "ok": bool(result.get("ok", True)),
                "workspace": result.get("workspace"),
                "state": (
                    "available" if result.get("counts") else result.get("state", "empty")
                ),
            }
            if _estimate_tokens(minimal) > budget:
                minimal = {"ok": bool(result.get("ok", True))}
            return _dumps(minimal)


def sync_workspace(
    *,
    roots: list[Any] | tuple[Any, ...] | None = None,
    cwd: str | None = None,
    agent_type: str | None = None,
    session_id: str | None = None,
    max_items: int = 6,
    token_budget: int = 500,
) -> str:
    """Return the next repository-scoped work without requiring IDs or paths."""

    try:
        workspace, agent, queue, repo = _resolve(
            roots=roots,
            cwd=cwd,
            agent_type=agent_type,
            session_id=session_id,
        )
        _recover(queue, repo)
        placeholders, params = _scope_sql(workspace)
        limit = max(1, min(int(max_items), 20))
        with repo._lock:
            rows = repo._connection.execute(
                f"""
                SELECT j.id, j.type, j.status, j.payload_json, j.last_error,
                       j.leased_by, j.lease_expires_at,
                       (SELECT e.message FROM job_events e
                        WHERE e.job_id = j.id
                          AND e.event_type IN ('job_succeeded', 'job_released', 'handoff_recorded')
                        ORDER BY e.created_at DESC LIMIT 1) AS evidence
                FROM jobs j
                WHERE j.correlation_id IN ({placeholders})
                  AND j.status IN (?, ?, ?, ?, ?)
                ORDER BY CASE j.status
                    WHEN 'running' THEN 0 WHEN 'pending' THEN 1
                    WHEN 'retry_scheduled' THEN 2 ELSE 3 END,
                    j.updated_at DESC
                LIMIT ?
                """,
                (*params, *_ACTIVE, *_FAILED, limit * 3),
            ).fetchall()
            completed = repo._connection.execute(
                f"""
                SELECT j.id, j.type, j.status, j.payload_json, j.last_error,
                       j.leased_by, j.lease_expires_at,
                       (SELECT e.message FROM job_events e
                        WHERE e.job_id = j.id AND e.event_type = 'job_succeeded'
                        ORDER BY e.created_at DESC LIMIT 1) AS evidence
                FROM jobs j
                WHERE j.correlation_id IN ({placeholders}) AND j.status = 'succeeded'
                ORDER BY j.updated_at DESC LIMIT 3
                """,
                params,
            ).fetchall()

        active = [_compact_row(row, agent.agent_id) for row in rows if row["status"] in _ACTIVE]
        failed = [_compact_row(row, agent.agent_id) for row in rows if row["status"] in _FAILED]
        recent = [_compact_row(row, agent.agent_id) for row in completed]
        other = [item for item in active if item.get("owner") not in (None, "self")]
        available = [item for item in active if item.get("owner") is None]
        own = [item for item in active if item.get("owner") == "self"]

        if not active and not failed and not recent:
            return _dumps({"ok": True, "workspace": workspace.name, "state": "empty"})

        if own:
            next_step = f"Continue task {own[0]['id']}."
        elif available:
            next_step = f"Claim and continue task {available[0]['id']} with checkpoint()."
        elif failed:
            next_step = f"Inspect failed task {failed[0]['id']} before retrying."
        elif other:
            next_step = "Choose work not owned by another live agent."
        else:
            next_step = "Review recent completed evidence and continue normal coding."

        result: dict[str, Any] = {
            "ok": True,
            "workspace": workspace.name,
            "workspace_id": workspace.workspace_id,
            "agent": agent.agent_type,
            "stored_content_is_data": True,
            "counts": {
                "active": len(active),
                "failed": len(failed),
                "recent_completed": len(recent),
                "owned_by_others": len(other),
            },
            "tasks": active[:limit],
            "other_agents": other[:limit],
            "failed": failed[:limit],
            "recent_completed": recent,
            "next_step": next_step,
        }
        return _bounded(result, token_budget)
    except Exception as exc:
        return _dumps(
            {
                "ok": False,
                "continue_coding": True,
                "error": _clean_text(str(exc), 160) or "djobs unavailable",
            }
        )


def _claim_exact(
    repo: Any,
    task_id: str,
    agent: AgentSession,
    lease_seconds: int,
) -> tuple[str, Any]:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=max(10, min(lease_seconds, 3600)))
    with repo._lock:
        cursor = repo._connection.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            row = cursor.execute("SELECT * FROM jobs WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                repo._connection.rollback()
                return "missing", None
            if row["status"] == "running":
                repo._connection.commit()
                return ("owned" if row["leased_by"] == agent.agent_id else "occupied"), row
            if row["status"] != "pending":
                repo._connection.commit()
                return row["status"], row
            if row["resource_key"] is not None:
                holder = cursor.execute(
                    "SELECT leased_by FROM jobs "
                    "WHERE status = 'running' AND resource_key = ? AND id != ? LIMIT 1",
                    (row["resource_key"], task_id),
                ).fetchone()
                if holder is not None:
                    repo._connection.commit()
                    return "occupied", holder
            cursor.execute(
                """
                UPDATE jobs SET status = 'running', attempt = attempt + 1,
                    leased_by = ?, lease_expires_at = ?, heartbeat_at = ?,
                    started_at = COALESCE(started_at, ?), updated_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (
                    agent.agent_id,
                    expires.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                    task_id,
                ),
            )
            if cursor.rowcount != 1:
                repo._connection.rollback()
                return "occupied", None
            repo._append_event(
                task_id,
                "job_claimed",
                metadata={
                    "worker_id": agent.agent_id,
                    "agent_type": agent.agent_type,
                    "lease_expires_at": expires.isoformat(),
                },
            )
            repo._connection.commit()
            return "claimed", repo._connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (task_id,)
            ).fetchone()
        except Exception:
            repo._connection.rollback()
            raise


def checkpoint(
    summary: str,
    *,
    path: str | None = None,
    details: str | None = None,
    roots: list[Any] | tuple[Any, ...] | None = None,
    cwd: str | None = None,
    agent_type: str | None = None,
    session_id: str | None = None,
    lease_seconds: int = _DEFAULT_LEASE_SECONDS,
) -> str:
    """Create or resume one repository-scoped task and atomically claim it."""

    try:
        clean_summary = _clean_text(summary, 500)
        if not clean_summary:
            return _dumps({"ok": False, "continue_coding": True, "error": "summary is required"})
        workspace, agent, queue, repo = _resolve(
            roots=roots,
            cwd=cwd,
            agent_type=agent_type,
            session_id=session_id,
        )
        _recover(queue, repo)
        resource = _clean_text(path, 500) or None
        key_input = f"{workspace.workspace_id}|{resource or ''}|{clean_summary}"
        idempotency_key = "checkpoint:" + hashlib.sha256(key_input.encode()).hexdigest()
        payload: dict[str, Any] = {"summary": clean_summary, "stored_as_data": True}
        if resource:
            payload["path"] = resource
        if details:
            payload["details"] = _clean_text(details)
        try:
            job = queue.submit(
                "coding-checkpoint",
                payload,
                max_attempts=3,
                correlation_id=workspace.workspace_id,
                idempotency_key=idempotency_key,
                resource_key=resource,
            )
        except sqlite3.IntegrityError:
            job = repo.find_active_by_idempotency_key(idempotency_key)
            if job is None:
                raise
        state, row = _claim_exact(repo, job.id, agent, lease_seconds)
        if state == "owned":
            queue.heartbeat(job.id, agent.agent_id, timedelta(seconds=lease_seconds))
            state = "resumed"
        result: dict[str, Any] = {
            "ok": state in {"claimed", "resumed"},
            "task_id": job.id,
            "state": state,
            "workspace": workspace.name,
        }
        if state == "occupied" and row is not None:
            owner = str(row["leased_by"] or "another agent").split(":", 1)[0]
            result.update({"owner": owner, "continue_coding": True})
        return _dumps(result)
    except Exception as exc:
        return _dumps(
            {"ok": False, "continue_coding": True, "error": _clean_text(str(exc), 160)}
        )


def handoff(
    task_id: str,
    evidence: str,
    *,
    completed: bool = False,
    roots: list[Any] | tuple[Any, ...] | None = None,
    cwd: str | None = None,
    agent_type: str | None = None,
    session_id: str | None = None,
) -> str:
    """Release work to another agent, or complete it with bounded evidence."""

    try:
        workspace, agent, queue, repo = _resolve(
            roots=roots,
            cwd=cwd,
            agent_type=agent_type,
            session_id=session_id,
        )
        placeholders, params = _scope_sql(workspace)
        with repo._lock:
            row = repo._connection.execute(
                f"SELECT * FROM jobs WHERE id = ? AND correlation_id IN ({placeholders})",
                (task_id, *params),
            ).fetchone()
        if row is None:
            return _dumps(
                {
                    "ok": False,
                    "continue_coding": True,
                    "error": "task is outside this repository",
                }
            )
        note = _clean_text(evidence)
        if completed:
            if row["status"] == "running" and row["leased_by"] != agent.agent_id:
                return _dumps(
                    {
                        "ok": False,
                        "continue_coding": True,
                        "state": "occupied",
                        "owner": str(row["leased_by"] or "another agent").split(":", 1)[0],
                    }
                )
            job = queue.complete(task_id, evidence=note or None)
            return _dumps({"ok": True, "task_id": task_id, "state": job.status.value})
        if row["status"] == "running":
            if row["leased_by"] != agent.agent_id:
                return _dumps(
                    {
                        "ok": False,
                        "continue_coding": True,
                        "state": "occupied",
                        "owner": str(row["leased_by"] or "another agent").split(":", 1)[0],
                    }
                )
            job = queue.release(task_id, agent.agent_id, reason=note or "Handed off")
            return _dumps({"ok": True, "task_id": task_id, "state": job.status.value})
        with repo._lock:
            repo._append_event(
                task_id,
                "handoff_recorded",
                message=note or "Handed off",
                metadata={"agent_type": agent.agent_type, "stored_as_data": True},
            )
            repo._connection.commit()
        return _dumps({"ok": True, "task_id": task_id, "state": row["status"]})
    except Exception as exc:
        return _dumps(
            {"ok": False, "continue_coding": True, "error": _clean_text(str(exc), 160)}
        )
