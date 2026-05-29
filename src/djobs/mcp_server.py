"""MCP server — exposes the durable job queue to AI agents via Model Context Protocol.

Tools
-----
- enqueue_task: Submit a durable task that survives crashes.
- claim_task: Atomically claim the next pending task (multi-agent).
- heartbeat_task: Renew the lease on a claimed task (multi-agent).
- release_task: Return a claimed task to the queue (multi-agent).
- register_agent: Register an agent so the fleet can see it is online (multi-agent).
- agent_heartbeat: Liveness ping so the queue knows an agent is alive (multi-agent).
- list_agents: Cross-agent fleet view of who is online and what they can do.
- check_task: Inspect a task by ID (status, attempts, errors, duration).
- list_tasks: List tasks by correlation_id, optionally filtered by status.
- resume_session: Find all incomplete tasks for a correlation_id (crash recovery).
- audit_log: Audit trail of job lifecycle events — "what did the AI do?".
- health: Queue health summary (depth by status, totals).

The server embeds a lightweight background daemon (WorkerPool + SchedulerLoop)
that auto-starts when the MCP process launches.  Registered built-in handlers
(e.g. ``echo``) are executed automatically; AI-powered tasks are handled by
the Copilot agent itself via the normal tool-call flow.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import threading
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP

from djobs.cli import BUILTIN_HANDLERS
from djobs.core.models import Agent, Job
from djobs.daemon import Daemon
from djobs.queue.service import QueueService
from djobs.storage.sqlite import SQLiteJobRepository

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Server singleton — initialised lazily on first call or via configure().
# ---------------------------------------------------------------------------

_server = FastMCP(
    "djobs",
    instructions=(
        "Durable job queue for AI agents. "
        "Use enqueue_task to submit long-running work that survives crashes. "
        "Use resume_session at the start of each conversation to recover in-progress tasks. "
        "For multi-agent workflows, agents share one queue: use claim_task to atomically "
        "take the next task, heartbeat_task to keep the lease alive, and complete_task / "
        "fail_task / release_task when done. Register each agent with register_agent and "
        "keep it alive with agent_heartbeat; list_agents shows the live fleet."
    ),
)

_queue: QueueService | None = None
_db_path: str = os.environ.get("DJOBS_DB") or "djobs_mcp.db"
_daemon: Daemon | None = None
_daemon_thread: threading.Thread | None = None


def configure(db_path: str | None = None) -> QueueService:
    """Initialise the QueueService backing the MCP tools.

    When ``db_path`` is omitted, honors the ``DJOBS_DB`` environment variable
    (for a shared global queue), falling back to the workspace-local default.
    """
    global _queue, _db_path
    _db_path = db_path or os.environ.get("DJOBS_DB") or "djobs_mcp.db"
    repo = SQLiteJobRepository.from_path(_db_path)
    _queue = QueueService(repo)
    return _queue


def _get_queue() -> QueueService:
    """Return current QueueService, auto-initialising if needed."""
    global _queue
    if _queue is None:
        configure(_db_path)
    assert _queue is not None
    return _queue


def _start_embedded_daemon() -> None:
    """Start a background daemon thread that processes built-in handlers.

    Called once during ``main()`` so the worker runs as long as the MCP
    server process lives.  Safe to call multiple times (no-op after first).
    """
    global _daemon, _daemon_thread
    if _daemon is not None:
        return  # already running

    q = _get_queue()
    from djobs.worker.registry import HandlerRegistry

    registry = HandlerRegistry()
    for job_type, handler in BUILTIN_HANDLERS.items():
        registry.register(job_type, handler)

    _daemon = Daemon(
        queue=q,
        registry=registry,
        max_concurrent=2,
        poll_interval=2.0,
        scheduler_interval=5.0,
    )

    stop = threading.Event()
    _daemon_thread = threading.Thread(
        target=_daemon.run_until,
        args=(stop,),
        daemon=True,
        name="djobs-embedded-daemon",
    )
    _daemon_thread.start()

    def _cleanup() -> None:
        stop.set()
        if _daemon_thread is not None:
            _daemon_thread.join(timeout=5)

    atexit.register(_cleanup)
    logger.info("Embedded daemon started (handlers: %s)", list(BUILTIN_HANDLERS))


def _job_to_dict(job: Job) -> dict[str, Any]:
    """Serialise a Job dataclass to a JSON-safe dict."""
    return {
        "id": job.id,
        "type": job.type,
        "status": job.status.value,
        "payload": job.payload,
        "attempt": job.attempt,
        "max_attempts": job.max_attempts,
        "correlation_id": job.correlation_id,
        "last_error": job.last_error,
        "run_after": job.run_after.isoformat() if job.run_after else None,
        "depends_on": job.depends_on,
        "resource_key": job.resource_key,
        "leased_by": job.leased_by,
        "lease_expires_at": job.lease_expires_at.isoformat() if job.lease_expires_at else None,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
    }


def _agent_to_dict(agent: Agent) -> dict[str, Any]:
    """Serialise an Agent dataclass to a JSON-safe dict."""
    return {
        "id": agent.id,
        "status": agent.status.value,
        "capabilities": agent.capabilities,
        "metadata": agent.metadata,
        "registered_at": agent.registered_at.isoformat(),
        "last_heartbeat_at": agent.last_heartbeat_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@_server.tool()
def enqueue_task(
    task_type: str,
    payload: str = "{}",
    max_attempts: int = 3,
    correlation_id: str | None = None,
    idempotency_key: str | None = None,
    depends_on: list[str] | None = None,
    resource_key: str | None = None,
) -> str:
    """Submit a durable task that survives agent/IDE crashes.

    Args:
        task_type: Category of work (e.g. "refactor", "add-docstrings", "test-gen").
        payload: JSON string with task-specific parameters.
        max_attempts: How many times to retry on failure (default 3).
        correlation_id: Groups related tasks (use workspace path or session id).
        idempotency_key: Prevents duplicate submission of the same task.
        depends_on: Optional list of task ids that must succeed before this task
                    becomes claimable. Use this to build a dependency DAG —
                    e.g. run tests only after all refactor tasks complete.
        resource_key: Optional exclusive-lock key (e.g. a file path). While a
                      task holding this key is running, no other task with the
                      same key is claimable — preventing two agents from editing
                      the same resource at once.

    Returns:
        JSON summary of the created task including its id.
    """
    q = _get_queue()
    parsed_payload = json.loads(payload) if isinstance(payload, str) else payload
    job = q.submit(
        job_type=task_type,
        payload=parsed_payload,
        max_attempts=max_attempts,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        depends_on=depends_on,
        resource_key=resource_key,
    )
    return json.dumps(_job_to_dict(job), indent=2)


@_server.tool()
def check_task(task_id: str) -> str:
    """Inspect a task by ID — status, attempts, errors, duration.

    Args:
        task_id: The UUID of the task to inspect.

    Returns:
        JSON inspection summary.
    """
    q = _get_queue()
    result = q.inspect(task_id)
    return json.dumps(result, indent=2, default=str)


@_server.tool()
def complete_task(task_id: str, evidence: str | None = None) -> str:
    """Mark a task as successfully completed.

    Call this after the AI agent has finished processing a task (e.g. editing
    a file).  Works for both pending (agent-managed) and running (daemon-managed)
    tasks.

    Args:
        task_id: The UUID of the task to mark as succeeded.
        evidence: Optional free-form summary of what was done (e.g.
                  "added docstrings to 3 functions in utils/helpers.py").
                  Stored in the audit trail for later review.

    Returns:
        JSON summary of the completed task.
    """
    q = _get_queue()
    job = q.complete(task_id, evidence=evidence)
    return json.dumps(_job_to_dict(job), indent=2)


@_server.tool()
def fail_task(task_id: str, error: str) -> str:
    """Mark a task as failed.

    Call this when the AI agent encounters an unrecoverable error while
    processing a task.

    **Security note:** The ``error`` string is stored in the database and
    visible via ``audit_log``.  Do not include secrets, credentials, or
    personally identifiable information in the error message.

    Args:
        task_id: The UUID of the task to mark as failed.
        error: Description of what went wrong.

    Returns:
        JSON summary of the failed task.
    """
    q = _get_queue()
    job = q.fail(task_id, error)
    return json.dumps(_job_to_dict(job), indent=2)


@_server.tool()
def claim_task(
    agent_id: str,
    task_types: list[str] | None = None,
    lease_seconds: int | None = None,
) -> str:
    """Atomically claim the next available task for this agent (multi-agent).

    Multiple agents (even from different vendors/IDEs) can share one djobs
    queue.  Each call hands exactly one pending task to exactly one agent —
    the claim is atomic, so two agents never get the same task.  The claimed
    task is leased to ``agent_id``; call ``heartbeat_task`` periodically to
    keep the lease alive, then ``complete_task`` / ``fail_task`` / ``release_task``
    when done.

    Args:
        agent_id: Stable identifier for the calling agent (used as the lease owner).
        task_types: Optional allow-list of task types this agent can handle.
                    Omit to claim any pending task.
        lease_seconds: Optional lease duration in seconds. Omit for the default.

    Returns:
        JSON summary of the claimed task, or a message if the queue is empty.
    """
    q = _get_queue()
    if lease_seconds is not None:
        job = q._repository.claim_next_job(
            agent_id,
            timedelta(seconds=lease_seconds),
            type_filter=task_types,
        )
    else:
        job = q.claim(agent_id, type_filter=task_types)
    if job is None:
        return json.dumps(
            {"claimed": False, "message": "No pending tasks available to claim."},
            indent=2,
        )
    return json.dumps({"claimed": True, "task": _job_to_dict(job)}, indent=2)


@_server.tool()
def heartbeat_task(task_id: str, agent_id: str, lease_seconds: int | None = None) -> str:
    """Renew the lease on a task this agent has claimed (multi-agent).

    Call periodically while working on a long task so other agents do not
    reclaim it.  If an agent crashes and stops sending heartbeats, the lease
    expires and the task is automatically returned to the queue.

    Args:
        task_id: The UUID of the claimed task.
        agent_id: The agent that holds the lease (must match the claimer).
        lease_seconds: Optional new lease duration in seconds.

    Returns:
        JSON summary of the task with its refreshed lease.
    """
    q = _get_queue()
    lease = timedelta(seconds=lease_seconds) if lease_seconds is not None else None
    job = q.heartbeat(task_id, agent_id, lease)
    return json.dumps(_job_to_dict(job), indent=2)


@_server.tool()
def release_task(task_id: str, agent_id: str, reason: str | None = None) -> str:
    """Release a claimed task back to the queue (multi-agent).

    Call this when an agent cannot make progress on a task it claimed, or is
    shutting down gracefully.  The task returns to ``pending`` so another
    agent can claim it immediately.  Only the agent holding the lease may
    release the task.

    Args:
        task_id: The UUID of the claimed task.
        agent_id: The agent that holds the lease (must match the claimer).
        reason: Optional free-form note on why the task was released
                (stored in the audit trail).

    Returns:
        JSON summary of the released task.
    """
    q = _get_queue()
    job = q.release(task_id, agent_id, reason)
    return json.dumps(_job_to_dict(job), indent=2)


@_server.tool()
def register_agent(
    agent_id: str,
    capabilities: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Register this agent with the queue so others can see it is online.

    Call once when an agent starts up.  Re-registering is safe (upsert) and
    brings the agent back online.  Declare which task types the agent can
    handle via ``capabilities`` so the fleet view shows who can do what.

    Args:
        agent_id: A stable identifier for this agent (reused across restarts).
        capabilities: Optional list of task types this agent can handle.
        metadata: Optional free-form info (hostname, pid, version, model).

    Returns:
        JSON summary of the registered agent.
    """
    q = _get_queue()
    agent = q.register_agent(agent_id, capabilities, metadata)
    return json.dumps(_agent_to_dict(agent), indent=2)


@_server.tool()
def agent_heartbeat(agent_id: str) -> str:
    """Send a liveness ping so the queue knows this agent is still alive.

    Call periodically (e.g. every 30s).  Agents that stop heartbeating are
    automatically marked offline so the fleet view reflects who is really
    available.  A heartbeat from a reaped agent brings it back online.

    Args:
        agent_id: The agent identifier used at registration.

    Returns:
        JSON summary of the agent with its refreshed heartbeat.
    """
    q = _get_queue()
    agent = q.agent_heartbeat(agent_id)
    return json.dumps(_agent_to_dict(agent), indent=2)


@_server.tool()
def list_agents(status: str | None = None) -> str:
    """List registered agents and their liveness — the cross-agent fleet view.

    Stale agents (no recent heartbeat) are reaped to offline before the list
    is returned, so the result reflects who is actually available right now.

    Args:
        status: Optional filter, e.g. ``"online"`` or ``"offline"``.

    Returns:
        JSON object with the reaped count and the list of agents.
    """
    q = _get_queue()
    reaped = q.reap_stale_agents()
    agents = q.list_agents(status)
    return json.dumps(
        {
            "reaped": [a.id for a in reaped],
            "count": len(agents),
            "agents": [_agent_to_dict(a) for a in agents],
        },
        indent=2,
    )


@_server.tool()
def list_tasks(
    correlation_id: str,
    status_filter: str | None = None,
) -> str:
    """List tasks grouped by a correlation_id.

    Args:
        correlation_id: The shared correlation id to filter by.
        status_filter: Optional status filter (pending, running, succeeded, failed,
                       retry_scheduled, dead_lettered). Omit for all.

    Returns:
        JSON array of matching tasks.
    """
    q = _get_queue()
    results: list[dict[str, Any]] = []

    # Use a direct query for correlation_id filtering
    repo: Any = q._repository
    if hasattr(repo, "_connection"):
        conn = repo._connection
        with repo._lock:
            if status_filter:
                rows = conn.execute(
                    "SELECT id FROM jobs WHERE correlation_id = ? AND status = ?",
                    (correlation_id, status_filter),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id FROM jobs WHERE correlation_id = ?",
                    (correlation_id,),
                ).fetchall()
        for row in rows:
            job = repo.get_job(row[0])
            if job is not None:
                results.append(_job_to_dict(job))
    else:
        return json.dumps({"error": "list_tasks requires SQLite backend"})

    return json.dumps(results, indent=2, default=str)


@_server.tool()
def resume_session(correlation_id: str) -> str:
    """Find all incomplete tasks for a correlation_id — crash recovery entry point.

    Call this at the start of every conversation to discover unfinished work from
    previous sessions that were interrupted.

    Args:
        correlation_id: Typically the workspace path or a stable session identifier.

    Returns:
        JSON with incomplete tasks and a summary.
    """
    q = _get_queue()
    repo: Any = q._repository

    incomplete_statuses = ("pending", "running", "retry_scheduled")

    tasks: list[dict[str, Any]] = []
    if hasattr(repo, "_connection"):
        conn = repo._connection
        with repo._lock:
            rows = conn.execute(
                "SELECT id FROM jobs WHERE correlation_id = ? AND status IN (?, ?, ?)",
                (correlation_id, *incomplete_statuses),
            ).fetchall()
        for row in rows:
            job = repo.get_job(row[0])
            if job is not None:
                tasks.append(_job_to_dict(job))
    else:
        return json.dumps({"error": "resume_session requires SQLite backend"})

    return json.dumps(
        {
            "correlation_id": correlation_id,
            "incomplete_count": len(tasks),
            "tasks": tasks,
            "message": (
                f"Found {len(tasks)} incomplete task(s). These survived from a previous session."
                if tasks
                else "No incomplete tasks. Starting fresh."
            ),
        },
        indent=2,
        default=str,
    )


@_server.tool()
def health() -> str:
    """Queue health summary — depth by status, total jobs.

    Returns:
        JSON health report.
    """
    q = _get_queue()
    result = q.health()
    return json.dumps(result, indent=2)


@_server.tool()
def audit_log(
    since: str | None = None,
    until: str | None = None,
    correlation_id: str | None = None,
    event_type: str | None = None,
    summary: bool = True,
    limit: int = 100,
) -> str:
    """Audit trail of job lifecycle events — answers "what did the AI do?".

    Use this to investigate AI agent actions across sessions: which tasks ran,
    which failed, when changes were made, and how the queue behaved over time.

    Args:
        since: ISO datetime lower bound (default: 24 hours ago).
        until: ISO datetime upper bound (default: now).
        correlation_id: Restrict to one workspace / session (default: all).
        event_type: Filter by event type, e.g. "job_failed" (default: all).
        summary: If True, return aggregated stats; if False, return event list.
        limit: Max events scanned (summary) or returned (detail). Default 100.

    Returns:
        JSON report. In summary mode: counts by event type, task counts by status
        and type, plus up to 5 recent failures with error messages. In detail mode:
        chronological list of events with task type and correlation_id.

    Examples:
        audit_log()                                 # summary of last 24h
        audit_log(summary=False, limit=50)          # last 50 detailed events
        audit_log(event_type="job_failed")          # only failures in last 24h
        audit_log(correlation_id="c:/my/workspace") # scoped to one workspace
    """
    q = _get_queue()
    repo: Any = q._repository
    if not hasattr(repo, "_connection"):
        return json.dumps({"error": "audit_log requires SQLite backend"})

    now = datetime.now(UTC)
    try:
        since_dt = datetime.fromisoformat(since) if since else now - timedelta(hours=24)
        until_dt = datetime.fromisoformat(until) if until else now
    except ValueError as exc:
        return json.dumps({"error": f"invalid datetime format: {exc}"})
    if since_dt.tzinfo is None:
        since_dt = since_dt.replace(tzinfo=UTC)
    if until_dt.tzinfo is None:
        until_dt = until_dt.replace(tzinfo=UTC)

    where_clauses = ["e.created_at >= ?", "e.created_at <= ?"]
    params: list[Any] = [since_dt.isoformat(), until_dt.isoformat()]
    if correlation_id:
        where_clauses.append("j.correlation_id = ?")
        params.append(correlation_id)
    if event_type:
        where_clauses.append("e.event_type = ?")
        params.append(event_type)

    where_sql = " AND ".join(where_clauses)
    base_sql = f"""
        SELECT
            e.id AS event_id,
            e.job_id AS job_id,
            e.event_type AS event_type,
            e.message AS message,
            e.created_at AS event_at,
            j.type AS task_type,
            j.status AS job_status,
            j.correlation_id AS correlation_id,
            j.created_at AS job_created_at,
            j.updated_at AS job_updated_at
        FROM job_events e
        JOIN jobs j ON e.job_id = j.id
        WHERE {where_sql}
        ORDER BY e.created_at DESC
        LIMIT ?
    """

    scan_limit = max(limit, 5000) if summary else limit
    with repo._lock:
        rows = repo._connection.execute(base_sql, (*params, scan_limit)).fetchall()

    if not summary:
        events_out = [
            {
                "event_id": row["event_id"],
                "job_id": row["job_id"],
                "task_type": row["task_type"],
                "event_type": row["event_type"],
                "message": row["message"],
                "correlation_id": row["correlation_id"],
                "at": row["event_at"],
            }
            for row in rows
        ]
        return json.dumps(
            {
                "since": since_dt.isoformat(),
                "until": until_dt.isoformat(),
                "filters": {
                    "correlation_id": correlation_id,
                    "event_type": event_type,
                },
                "count": len(events_out),
                "truncated": len(events_out) == limit,
                "events": events_out,
            },
            indent=2,
            default=str,
        )

    # Summary mode — aggregate scanned rows.
    events_by_type: Counter[str] = Counter()
    tasks_seen: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    for row in rows:
        events_by_type[row["event_type"]] += 1
        jid = row["job_id"]
        if jid not in tasks_seen:
            tasks_seen[jid] = {
                "type": row["task_type"],
                "status": row["job_status"],
            }
        if row["event_type"] == "job_failed" and len(failures) < 5:
            failures.append(
                {
                    "job_id": jid,
                    "type": row["task_type"],
                    "error": row["message"],
                    "at": row["event_at"],
                }
            )

    tasks_by_status: Counter[str] = Counter()
    tasks_by_type: Counter[str] = Counter()
    for t in tasks_seen.values():
        tasks_by_status[t["status"]] += 1
        tasks_by_type[t["type"]] += 1

    return json.dumps(
        {
            "since": since_dt.isoformat(),
            "until": until_dt.isoformat(),
            "filters": {
                "correlation_id": correlation_id,
                "event_type": event_type,
            },
            "total_events": sum(events_by_type.values()),
            "events_by_type": dict(events_by_type),
            "tasks": {
                "total": len(tasks_seen),
                "by_status": dict(tasks_by_status),
                "by_type": dict(tasks_by_type),
            },
            "recent_failures": failures,
            "scan_truncated": len(rows) == scan_limit,
        },
        indent=2,
        default=str,
    )


# ---------------------------------------------------------------------------
# Entry point — stdio transport for VS Code MCP integration
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the MCP server over stdio (used by VS Code).

    Also starts the embedded background daemon so built-in handlers
    are processed automatically — zero user setup required.
    """
    _get_queue()  # ensure db is initialised
    _start_embedded_daemon()  # background worker for built-in handlers
    _server.run(transport="stdio")


if __name__ == "__main__":
    main()
