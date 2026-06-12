"""CLI entry point for the distributed job system.

Commands::

    djobs serve              Start the background daemon
    djobs serve --db my.db   Use a custom database path
    djobs serve --workers 8  Set max concurrent workers
    djobs mcp                Run the MCP server over stdio (for agents / uvx)
    djobs dashboard          Serve the read-only cross-agent web dashboard
    djobs init               One-command setup (mcp.json + instructions + doctor)
    djobs install-mcp        Print an mcp.json snippet for VS Code
    djobs install-instructions  Create/update the agent guidance block
    djobs doctor             Diagnose setup (interpreter, wiring, db)
    djobs explain            Explain why each still-visible task is in the queue
    djobs token-savings      Estimate token savings from durable task evidence
    djobs audit              Query the audit trail from the terminal
"""

from __future__ import annotations

import argparse
import importlib
import logging
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from djobs.core.constants import STALE_AFTER_DAYS
from djobs.core.correlation import correlation_id_variants

Handler = Callable[[dict[str, Any]], Any]


def _default_db() -> str:
    """Resolve the default database path.

    Honors the ``DJOBS_DB`` environment variable so a single global queue can
    be shared across projects and Python environments. Falls back to the
    workspace-local ``djobs_mcp.db`` when unset.
    """
    return os.environ.get("DJOBS_DB") or "djobs_mcp.db"


def _global_db() -> str:
    """Return the path to the shared global queue (``~/.djobs/global.db``)."""
    from pathlib import Path

    return str(Path.home() / ".djobs" / "global.db")


# Tolerant correlation_id matching and the stale threshold live in djobs.core so
# the CLI, MCP server, and VS Code extension cannot drift apart. The private
# aliases keep existing call sites unchanged.
_correlation_id_variants = correlation_id_variants
_STALE_AFTER_DAYS = STALE_AFTER_DAYS


def _parse_dt(value: Any) -> datetime | None:
    """Parse an ISO datetime string to an aware UTC datetime, or None."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _explain_visible_task(
    task: dict[str, Any],
    dep_status: dict[str, str],
    resource_holders: dict[str, str],
    now: datetime,
) -> dict[str, Any]:
    """Annotate a non-terminal task with a plain-language ``reason`` it is still
    visible, plus a machine-friendly ``category``.

    Mirrors the queue's real claim gating so the explanation matches what
    actually happens: a dependency blocks unless it has *succeeded*; a pending
    task is also gated by a future ``run_after`` and by a ``resource_key`` held
    by a running task. The task dict is mutated in place and returned.
    """
    status = task["status"]
    attempt = task.get("attempt") or 0
    max_attempts = task.get("max_attempts") or 0

    def _err() -> str:
        lines = (task.get("last_error") or "").strip().splitlines()
        first = lines[0] if lines else ""
        return (first[:117] + "...") if len(first) > 120 else first

    if status == "dead_lettered":
        msg = _err()
        task["category"] = "dead_lettered"
        task["reason"] = (
            f"Gave up after {attempt}/{max_attempts} attempts (dead-lettered)."
            + (f" Last error: {msg}." if msg else "")
            + " Archive it, or fix the cause and re-enqueue."
        )
        return task

    if status == "failed":
        msg = _err()
        task["category"] = "failed"
        task["reason"] = (
            f"Failed on attempt {attempt}/{max_attempts} and is not scheduled to retry."
            + (f" Last error: {msg}." if msg else "")
            + " Archive it, or re-enqueue to try again."
        )
        return task

    if status == "retry_scheduled":
        when = _parse_dt(task.get("run_after"))
        at = f" at {when.isoformat(timespec='seconds')}" if when else ""
        task["category"] = "retry_scheduled"
        task["reason"] = f"Waiting to retry (attempt {attempt}/{max_attempts}){at}."
        return task

    if status == "running":
        lease = _parse_dt(task.get("lease_expires_at"))
        leased_by = task.get("leased_by") or "a worker"
        if lease and lease < now:
            task["category"] = "lease_expired"
            task["reason"] = (
                f"Was claimed by {leased_by} but the lease expired "
                f"{lease.isoformat(timespec='seconds')} (the worker likely died). "
                "It will be recovered and retried automatically."
            )
        else:
            until = f" (lease until {lease.isoformat(timespec='seconds')})" if lease else ""
            task["category"] = "running"
            task["reason"] = f"Currently running on {leased_by}{until}."
        return task

    # status == pending
    deps = task.get("depends_on") or []
    blocking = [d for d in deps if dep_status.get(d) != "succeeded"]
    if blocking:
        # A dependency that is failed/dead/archived/missing can never succeed, so
        # the task is permanently stuck, not merely waiting — call that out.
        stuck = [
            f"{d[:8]} ({dep_status.get(d, 'missing')})"
            for d in blocking
            if dep_status.get(d) in (None, "failed", "dead_lettered", "archived")
        ]
        detail = f"Blocked: {len(blocking)} dependency task(s) have not succeeded."
        if stuck:
            detail += (
                " These will never succeed: "
                + ", ".join(stuck)
                + " — re-enqueue the dependency or archive this workflow."
            )
        task["category"] = "blocked"
        task["blocked_by"] = blocking
        task["reason"] = detail
        return task

    run_after = _parse_dt(task.get("run_after"))
    if run_after and run_after > now:
        task["category"] = "scheduled"
        task["reason"] = (
            f"Scheduled to start at {run_after.isoformat(timespec='seconds')}; not due yet."
        )
        return task

    resource_key = task.get("resource_key")
    if resource_key and resource_key in resource_holders:
        holder = resource_holders[resource_key]
        task["category"] = "resource_wait"
        task["reason"] = (
            f"Waiting for the exclusive resource '{resource_key}', currently held by "
            f"running task {holder[:8]}."
        )
        return task

    created = _parse_dt(task.get("created_at"))
    age_days = (now - created).days if created else 0
    if age_days >= _STALE_AFTER_DAYS:
        task["category"] = "stale"
        task["stale"] = True
        task["age_days"] = age_days
        task["reason"] = (
            f"Ready to run, but it has been pending {age_days} days — usually an "
            "abandoned workflow. Archive it if it is no longer needed."
        )
    else:
        task["category"] = "ready"
        task["reason"] = "Ready to run now; the agent simply has not picked it up yet."
    return task


# ---------------------------------------------------------------------------
# Built-in handlers
# ---------------------------------------------------------------------------


def _echo_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Built-in handler that logs and returns the payload (for testing)."""
    logging.getLogger("djobs.handlers.echo").info("echo: %s", payload)
    return {"echoed": payload}


BUILTIN_HANDLERS: dict[str, Handler] = {
    "echo": _echo_handler,
}


# ---------------------------------------------------------------------------
# Handler loading
# ---------------------------------------------------------------------------


def _load_handlers_module(dotted_path: str) -> dict[str, Handler]:
    """Import a module and collect its HANDLERS dict.

    The module must define ``HANDLERS: dict[str, Callable]``.
    """
    module = importlib.import_module(dotted_path)
    handlers = getattr(module, "HANDLERS", None)
    if handlers is None:
        raise ImportError(f"Module {dotted_path!r} does not export a HANDLERS dict")
    if not isinstance(handlers, dict):
        raise TypeError(
            f"HANDLERS in {dotted_path!r} must be a dict, got {type(handlers).__name__}"
        )
    return handlers


# ---------------------------------------------------------------------------
# serve command
# ---------------------------------------------------------------------------


def _cmd_serve(args: argparse.Namespace) -> None:
    """Run the background daemon."""
    from djobs.daemon import Daemon

    # Collect handlers: builtins + user-provided modules.
    handlers: dict[str, Handler] = dict(BUILTIN_HANDLERS)
    for module_path in args.handlers or []:
        loaded = _load_handlers_module(module_path)
        handlers.update(loaded)

    daemon = Daemon.from_db(
        db_path=args.db,
        handlers=handlers,
        max_concurrent=args.workers,
        poll_interval=args.poll_interval,
        scheduler_interval=args.scheduler_interval,
    )

    registered = ", ".join(sorted(handlers)) or "(none)"
    print(
        f"djobs daemon starting\n"
        f"  db:         {args.db}\n"
        f"  workers:    {args.workers}\n"
        f"  handlers:   {registered}\n"
        f"  worker_id:  {daemon.worker_id}\n"
    )

    daemon.run()


# ---------------------------------------------------------------------------
# mcp command
# ---------------------------------------------------------------------------


def _cmd_mcp(args: argparse.Namespace) -> None:
    """Run the djobs MCP server over stdio.

    This is the same server as the ``djobs-mcp`` console script, exposed as a
    subcommand so it can be launched as ``djobs mcp`` — which lets the MCP
    Registry / ``uvx djobs mcp`` start the server while keeping ``djobs`` (the
    real PyPI package) as the verifiable package identifier. The server honors
    the ``DJOBS_DB`` environment variable; pass ``--db`` to override it.
    """
    from djobs.mcp_server import configure
    from djobs.mcp_server import main as run_mcp_server

    if getattr(args, "db", None):
        configure(args.db)
    run_mcp_server()


# ---------------------------------------------------------------------------
# dashboard command
# ---------------------------------------------------------------------------


def _cmd_dashboard(args: argparse.Namespace) -> None:
    """Serve the read-only cross-agent web dashboard."""
    from djobs.dashboard import serve_dashboard

    print(
        f"djobs dashboard starting\n"
        f"  db:    {args.db}\n"
        f"  url:   http://{args.host}:{args.port}\n"
        f"  (read-only cross-agent view — Ctrl+C to stop)\n"
    )
    serve_dashboard(
        args.db,
        host=args.host,
        port=args.port,
        refresh_seconds=args.refresh,
    )


# ---------------------------------------------------------------------------
# status command
# ---------------------------------------------------------------------------


def _latest_evidence_by_job(repo: Any) -> dict[str, str | None]:
    """Map each job id to its most recent ``job_succeeded`` evidence message.

    Shared by ``status`` and ``token-savings`` so the evidence lookup cannot
    drift between the two commands. Ordered newest-first; ``setdefault`` keeps
    the latest message per job (``None`` when that completion had no evidence).
    """
    evidence: dict[str, str | None] = {}
    with repo._lock:
        rows = repo._connection.execute(
            """
            SELECT job_id, message
            FROM job_events
            WHERE event_type = 'job_succeeded'
            ORDER BY created_at DESC
            """
        ).fetchall()
    for row in rows:
        evidence.setdefault(row["job_id"], row["message"])
    return evidence


def _cmd_status(args: argparse.Namespace) -> None:
    """JSON snapshot for the VS Code extension."""
    import json
    from datetime import UTC, datetime

    from djobs.storage.sqlite import SQLiteJobRepository

    repo = SQLiteJobRepository.from_path(args.db)
    q_module = __import__("djobs.queue.service", fromlist=["QueueService"])
    queue = q_module.QueueService(repo)

    health_data = queue.health()

    evidence_by_job = _latest_evidence_by_job(repo)

    tasks: list[dict[str, Any]] = []
    with repo._lock:
        if args.correlation_id:
            cids = _correlation_id_variants(args.correlation_id)
            cid_ph = ",".join("?" for _ in cids)
            rows = repo._connection.execute(
                "SELECT id, type, status, payload_json, correlation_id, "
                "created_at, updated_at, attempt, max_attempts, last_error, "
                "depends_on_json "
                f"FROM jobs WHERE correlation_id IN ({cid_ph}) AND status != ? "
                "ORDER BY created_at",
                (*cids, "archived"),
            ).fetchall()
        else:
            rows = repo._connection.execute(
                "SELECT id, type, status, payload_json, correlation_id, "
                "created_at, updated_at, attempt, max_attempts, last_error, "
                "depends_on_json "
                "FROM jobs WHERE status != ? ORDER BY created_at",
                ("archived",),
            ).fetchall()
    for row in rows:
        item = dict(row)
        item["evidence"] = evidence_by_job.get(item["id"])
        raw_depends = item.pop("depends_on_json", None)
        item["depends_on"] = json.loads(raw_depends) if raw_depends else []
        tasks.append(item)

    result = {
        "timestamp": datetime.now(UTC).isoformat(),
        "health": health_data,
        "tasks": tasks,
    }
    print(json.dumps(result, indent=2, default=str))


def _cmd_skip(args: argparse.Namespace) -> None:
    """Mark a task as intentionally skipped/accepted without editing it."""
    import json

    from djobs.queue.service import QueueService
    from djobs.storage.sqlite import SQLiteJobRepository

    repo = SQLiteJobRepository.from_path(args.db)
    queue = QueueService(repo)
    evidence = args.evidence or "Skipped by user/operator"
    job = queue.complete(args.job_id, evidence=evidence)
    print(json.dumps({"id": job.id, "status": job.status.value, "evidence": evidence}, indent=2))


def _cmd_accept_before(args: argparse.Namespace) -> None:
    """Mark all earlier tasks in a workflow as accepted so resume can start later."""
    import json

    from djobs.queue.service import QueueService
    from djobs.storage.sqlite import SQLiteJobRepository

    repo = SQLiteJobRepository.from_path(args.db)
    queue = QueueService(repo)

    with repo._lock:
        target = repo._connection.execute(
            "SELECT rowid, correlation_id FROM jobs WHERE id = ?",
            (args.job_id,),
        ).fetchone()
        if target is None:
            raise SystemExit(f"Job not found: {args.job_id}")
        # Use the monotonic rowid (strict insertion order) rather than created_at,
        # which can collide for jobs created within the same clock tick.
        rows = repo._connection.execute(
            """
            SELECT id, status FROM jobs
            WHERE correlation_id = ?
              AND rowid < ?
            ORDER BY rowid ASC
            """,
            (target["correlation_id"], target["rowid"]),
        ).fetchall()

    accepted_ids: list[str] = []
    for row in rows:
        if row["status"] in {"succeeded", "archived"}:
            continue
        queue.complete(row["id"], evidence=args.evidence or f"Accepted before {args.job_id}")
        accepted_ids.append(row["id"])

    print(json.dumps({"accepted": accepted_ids, "count": len(accepted_ids)}, indent=2))


def _cmd_archive_workflow(args: argparse.Namespace) -> None:
    """Archive all non-terminal tasks in a workflow/session."""
    import json

    from djobs.queue.service import QueueService
    from djobs.storage.sqlite import SQLiteJobRepository

    repo = SQLiteJobRepository.from_path(args.db)
    queue = QueueService(repo)

    with repo._lock:
        if args.correlation_id:
            rows = repo._connection.execute(
                "SELECT id, status FROM jobs WHERE correlation_id = ? ORDER BY created_at ASC",
                (args.correlation_id,),
            ).fetchall()
        else:
            rows = repo._connection.execute(
                "SELECT id, status FROM jobs ORDER BY created_at ASC"
            ).fetchall()

    archived: list[str] = []
    skipped_terminal: list[str] = []
    for row in rows:
        if row["status"] in {"succeeded", "archived"}:
            skipped_terminal.append(row["id"])
            continue
        queue.archive(row["id"], reason=args.reason or "Archived by user/operator")
        archived.append(row["id"])

    print(
        json.dumps(
            {"archived": archived, "skipped": skipped_terminal, "count": len(archived)},
            indent=2,
        )
    )


def _cmd_explain(args: argparse.Namespace) -> None:
    """Explain, in plain language, why each still-visible task is in the queue.

    Answers the recurring "why is this old task still here?" question by
    reporting the real reason each non-terminal task has not completed —
    blocked by dependencies, scheduled for later, waiting on a resource lock,
    running (or orphaned by a dead worker), failed/dead-lettered, or simply
    pending and possibly stale. This is the read-only companion to
    skip / accept-before / archive-workflow, which act on what it surfaces.
    """
    import json
    from collections import Counter
    from itertools import groupby

    from djobs.storage.sqlite import SQLiteJobRepository

    repo = SQLiteJobRepository.from_path(args.db)
    now = datetime.now(UTC)

    columns = (
        "id, type, status, correlation_id, created_at, run_after, "
        "attempt, max_attempts, last_error, leased_by, lease_expires_at, "
        "depends_on_json, resource_key"
    )
    with repo._lock:
        if args.correlation_id:
            rows = repo._connection.execute(
                f"SELECT {columns} FROM jobs "
                "WHERE status NOT IN ('succeeded', 'archived') AND correlation_id = ? "
                "ORDER BY created_at",
                (args.correlation_id,),
            ).fetchall()
        else:
            rows = repo._connection.execute(
                f"SELECT {columns} FROM jobs "
                "WHERE status NOT IN ('succeeded', 'archived') "
                "ORDER BY correlation_id, created_at",
            ).fetchall()

        # Resolve every dependency's status and which running task holds each
        # resource key, in one query each, so the per-task explanation matches
        # the queue's real claim gating without an N+1 query pattern.
        dep_ids: set[str] = set()
        for r in rows:
            raw = r["depends_on_json"]
            if raw:
                dep_ids.update(json.loads(raw))
        dep_status: dict[str, str] = {}
        if dep_ids:
            placeholders = ", ".join("?" * len(dep_ids))
            for d in repo._connection.execute(
                f"SELECT id, status FROM jobs WHERE id IN ({placeholders})",
                tuple(dep_ids),
            ).fetchall():
                dep_status[d["id"]] = d["status"]
        resource_holders: dict[str, str] = {}
        for r in repo._connection.execute(
            "SELECT id, resource_key FROM jobs "
            "WHERE status = 'running' AND resource_key IS NOT NULL ORDER BY created_at",
        ).fetchall():
            resource_holders.setdefault(r["resource_key"], r["id"])

    tasks: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        raw_depends = item.pop("depends_on_json", None)
        item["depends_on"] = json.loads(raw_depends) if raw_depends else []
        tasks.append(_explain_visible_task(item, dep_status, resource_holders, now))

    by_category: Counter[str] = Counter(t["category"] for t in tasks)

    if args.output_format == "json":
        print(
            json.dumps(
                {
                    "timestamp": now.isoformat(),
                    "visible_count": len(tasks),
                    "by_category": dict(by_category),
                    "tasks": tasks,
                },
                indent=2,
                default=str,
            )
        )
        return

    if not tasks:
        scope = f" for correlation_id {args.correlation_id}" if args.correlation_id else ""
        print(f"No visible (non-terminal) tasks{scope}. Nothing is waiting.")
        return

    sorted_tasks = (
        tasks
        if args.correlation_id
        else sorted(tasks, key=lambda t: t.get("correlation_id") or "")
    )
    workflows = 0
    for cid, group in groupby(sorted_tasks, key=lambda t: t.get("correlation_id") or ""):
        workflows += 1
        members = list(group)
        label = cid or "(no correlation_id)"
        print(f"\nWorkflow: {label}  ({len(members)} visible task(s))")
        for t in members:
            print(f"  [{t['category']}] {t['id'][:8]}  {t['type']}")
            print(f"      {t['reason']}")

    print("\nSummary:")
    for cat, count in by_category.most_common():
        print(f"  {cat:15s} {count}")
    print(f"\n{len(tasks)} visible task(s) across {workflows} workflow(s).")


def _estimate_tokens(text: str, chars_per_token: float) -> int:
    """Rough token estimate using a configurable chars/token ratio."""
    import math

    if not text:
        return 0
    return max(1, math.ceil(len(text) / chars_per_token))


def _payload_summary(payload_json: str | None) -> str:
    """Return human-readable task payload fields for estimates."""
    import json

    if not payload_json:
        return ""
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError:
        return payload_json
    if not isinstance(payload, dict):
        return payload_json
    fields: list[str] = []
    for key in ("summary", "title", "name", "description", "file", "why", "condition"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            fields.append(value.strip())
    return " | ".join(fields) if fields else json.dumps(payload, separators=(",", ":"))


def _cmd_token_savings(args: argparse.Namespace) -> None:
    """Estimate how many prompt tokens djobs avoids during resume/recovery.

    This is intentionally an estimate, not metering. It compares a conservative
    "without djobs" replay model (the agent re-reads/re-plans each completed
    step after a reset) with the compact durable evidence djobs keeps for those
    completed steps. The assumptions are printed so the number is explainable
    and suitable for product experiments.
    """
    import json

    from djobs.storage.sqlite import SQLiteJobRepository

    if args.chars_per_token <= 0:
        raise SystemExit("--chars-per-token must be greater than 0")
    if args.redo_overhead_tokens < 0:
        raise SystemExit("--redo-overhead-tokens must be 0 or greater")

    repo = SQLiteJobRepository.from_path(args.db)
    evidence_by_job = _latest_evidence_by_job(repo)

    with repo._lock:
        if args.correlation_id:
            cids = _correlation_id_variants(args.correlation_id)
            placeholders = ",".join("?" for _ in cids)
            rows = repo._connection.execute(
                "SELECT id, type, status, payload_json, correlation_id, last_error "
                f"FROM jobs WHERE correlation_id IN ({placeholders}) AND status != ? "
                "ORDER BY created_at",
                (*cids, "archived"),
            ).fetchall()
        else:
            rows = repo._connection.execute(
                "SELECT id, type, status, payload_json, correlation_id, last_error "
                "FROM jobs WHERE status != ? ORDER BY correlation_id, created_at",
                ("archived",),
            ).fetchall()

    completed: list[dict[str, Any]] = []
    incomplete = 0
    estimated_replay_tokens = 0
    estimated_djobs_tokens = 0

    for row in rows:
        payload_summary = _payload_summary(row["payload_json"])
        evidence = evidence_by_job.get(row["id"]) or ""
        durable_text = "\n".join(
            part
            for part in (
                row["type"],
                row["status"],
                payload_summary,
                evidence,
                row["last_error"] or "",
            )
            if part
        )
        durable_tokens = _estimate_tokens(durable_text, args.chars_per_token)

        if row["status"] == "succeeded":
            replay_text = "\n".join(
                part for part in (row["type"], row["payload_json"] or "", evidence) if part
            )
            replay_tokens = args.redo_overhead_tokens + _estimate_tokens(
                replay_text,
                args.chars_per_token,
            )
            saved = max(0, replay_tokens - durable_tokens)
            estimated_replay_tokens += replay_tokens
            estimated_djobs_tokens += durable_tokens
            completed.append(
                {
                    "id": row["id"],
                    "type": row["type"],
                    "correlation_id": row["correlation_id"],
                    "estimated_replay_tokens": replay_tokens,
                    "estimated_djobs_evidence_tokens": durable_tokens,
                    "estimated_saved_tokens": saved,
                    "has_evidence": bool(evidence),
                }
            )
        else:
            incomplete += 1

    estimated_saved = max(0, estimated_replay_tokens - estimated_djobs_tokens)
    pct = (
        round((estimated_saved / estimated_replay_tokens) * 100, 1)
        if estimated_replay_tokens
        else 0.0
    )
    result = {
        "task_count": len(rows),
        "completed_count": len(completed),
        "incomplete_count": incomplete,
        "estimated_without_djobs_replay_tokens": estimated_replay_tokens,
        "estimated_with_djobs_evidence_tokens": estimated_djobs_tokens,
        "estimated_saved_tokens": estimated_saved,
        "estimated_saved_percent": pct,
        "assumptions": {
            "chars_per_token": args.chars_per_token,
            "redo_overhead_tokens_per_completed_task": args.redo_overhead_tokens,
            "model": "completed tasks would otherwise need replay/re-read/re-plan "
            "after context loss; djobs keeps compact evidence instead",
        },
        "completed_tasks": completed,
    }

    if args.output_format == "json":
        print(json.dumps(result, indent=2, default=str))
        return

    scope = f" for {args.correlation_id}" if args.correlation_id else ""
    print(f"djobs token savings estimate{scope}")
    print(f"  tasks:       {len(rows)} total, {len(completed)} completed, {incomplete} incomplete")
    print(f"  without:     {estimated_replay_tokens} replay token(s)")
    print(f"  with djobs:  {estimated_djobs_tokens} evidence token(s)")
    print(f"  saved:       {estimated_saved} token(s) ({pct}%)")
    print("\nAssumptions:")
    print(f"  chars/token: {args.chars_per_token}")
    print(f"  redo cost:   {args.redo_overhead_tokens} token(s) per completed task")
    print("  model:       completed task context is not replayed after resume")


# ---------------------------------------------------------------------------
# install-mcp command
# ---------------------------------------------------------------------------


_DJOBS_INSTRUCTIONS_START = "<!-- djobs:start -->"
_DJOBS_INSTRUCTIONS_END = "<!-- djobs:end -->"

_DJOBS_INSTRUCTIONS_BODY = """\
## djobs — durable job queue (auto-managed)

This workspace has the djobs MCP server wired in. Use it proactively so work
survives crashes, IDE reloads, and interrupted sessions. Do NOT wait to be
told — these are part of normal good behaviour here.

**Why this saves tokens:** persisting task state to djobs lets you recover work
with a single `resume_session` call instead of re-reading files and re-planning
from scratch after a crash or context reset. It also keeps progress out of the
chat context, so long multi-step tasks stay cheap and avoid context overflow.

- **Start every coding session with `resume_session`.** Before editing files,
    call `resume_session` with the workspace/session `correlation_id`. If it
    returns unfinished tasks, continue those first instead of starting from chat
    memory. If it returns nothing, proceed normally for small work or create a
    durable plan for larger work.
- **Plan before editing long or multi-step work.** When a request touches
    multiple files, has several steps, needs tests/docs/release packaging, or
    could be interrupted, call `enqueue_task` before the first edit. Create one
    task per meaningful unit with a stable `correlation_id` and `idempotency_key`
    (e.g. `"{task_type}:{file}"`) so progress is visible in the djobs sidebar and
    resuming after a crash re-runs nothing that already succeeded.
- **Close the loop with evidence.** As each unit finishes, call
  `complete_task(task_id, evidence="what changed")` — or `fail_task(task_id,
  error)` on failure. Always pass `evidence`: that one-line record is what lets
  a later session or a human verify the work and trust `resume_session` instead
  of re-doing it, and it is what the sidebar and `audit_log` show.
- **Make every task self-explanatory.** The sidebar shows the task `type` and a
  one-line summary — never make a human guess what an opaque id means. In each
  `enqueue_task` payload include human-readable fields:
  `summary` (what this step does, in plain language), `why` (why it is needed),
  and `condition` (what must be true before it can run, if it waits on data or
  state). Use a clear, descriptive `task_type` too. Example payload:
  `{"summary": "Wait for >=30 settled samples", "why": "3-axis judgment needs
  enough data to be statistically valid", "condition": "settled_count >= 30"}`.
- **Don't over-use it.** Short answers, single-file edits, quick questions, and
  trivial one-step tasks do NOT need the queue. Keep the chat fast.
- **Inspect when asked about progress.** Use `check_task`, `list_tasks`, and
  `audit_log` to report what was done and what remains.
"""


# Instruction-file targets the agent reads. Mapping is reused by both
# `install-instructions` and `init`.
_INSTRUCTION_TARGETS = {
    "copilot": ".github/copilot-instructions.md",
    "agent-md": ".agent.md",
}


def _render_instructions_block() -> str:
    """Return the full djobs managed guidance block, including sentinel markers."""
    return f"{_DJOBS_INSTRUCTIONS_START}\n{_DJOBS_INSTRUCTIONS_BODY}{_DJOBS_INSTRUCTIONS_END}\n"


def _resolve_instruction_targets(target: str) -> list[Path]:
    """Map a ``--target`` choice to the instruction file path(s) to write."""
    if target == "all":
        return [Path(p) for p in _INSTRUCTION_TARGETS.values()]
    return [Path(_INSTRUCTION_TARGETS[target])]


def _write_instructions_to(target: Path) -> None:
    """Create/update the djobs managed block in *target* idempotently.

    Uses sentinel markers so re-running only updates the djobs block and never
    touches the user's other instructions:

    - missing file  -> create it with the block;
    - file present, no djobs block -> append the block;
    - file present with a djobs block -> replace only that block in place.
    """
    block = _render_instructions_block()

    if target.exists():
        existing = target.read_text(encoding="utf-8")
        start = existing.find(_DJOBS_INSTRUCTIONS_START)
        end = existing.find(_DJOBS_INSTRUCTIONS_END)
        if start != -1 and end != -1 and end > start:
            # Replace the existing managed block in place.
            end_full = end + len(_DJOBS_INSTRUCTIONS_END)
            # Swallow a single trailing newline after the end marker if present.
            if end_full < len(existing) and existing[end_full] == "\n":
                end_full += 1
            new_content = existing[:start] + block + existing[end_full:]
            if new_content != existing:
                target.write_text(new_content, encoding="utf-8")
                print(f"Updated djobs guidance in {target}")
            else:
                print(f"djobs guidance already up to date in {target}")
            return
        # No managed block yet — append, ensuring a blank line separator.
        sep = "" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
        target.write_text(existing + sep + block, encoding="utf-8")
        print(f"Added djobs guidance to {target}")
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(block, encoding="utf-8")
    print(f"Created {target} with djobs guidance")


def _write_instructions_block() -> None:
    """Write the djobs block to ``.github/copilot-instructions.md``.

    Backward-compatible wrapper around :func:`_write_instructions_to` used by
    ``install-mcp`` (which writes the default Copilot instruction file).
    """
    _write_instructions_to(Path(_INSTRUCTION_TARGETS["copilot"]))


def _cmd_install_instructions(args: argparse.Namespace) -> None:
    """Create/update the djobs agent guidance block without touching mcp.json."""
    if getattr(args, "print", False):
        print(_render_instructions_block(), end="")
        return

    for target in _resolve_instruction_targets(args.target):
        _write_instructions_to(target)


def _resolve_mcp_command(args: argparse.Namespace) -> tuple[str, list[str]]:
    """Resolve the launch ``(command, args)`` for the MCP server.

    The goal is a wiring that *just works* in any project, even one without a
    workspace-local ``.venv``. Resolution order (first match wins):

    1. ``--command`` — use the given string verbatim as the launch command.
    2. ``--python`` — launch ``<python> -m djobs.mcp_server``.
    3. ``--portable`` — emit the relocatable ``${workspaceFolder}/.venv``
       interpreter hint (legacy behaviour). Useful when committing mcp.json to
       a shared repo whose collaborators each have a project-local venv with
       djobs installed.
    4. Default — prefer the installed ``djobs-mcp`` console script if it is on
       PATH (the case after ``pipx install djobs`` / ``pip install djobs``);
       otherwise fall back to the *absolute* path of the current interpreter
       (``sys.executable``), which is guaranteed to have djobs importable
       because that is exactly what is running this command.
    """
    import shutil

    command = getattr(args, "command", None)
    if command:
        return command, []

    python = getattr(args, "python", None)
    if python:
        return python, ["-m", "djobs.mcp_server"]

    if getattr(args, "portable", False):
        if os.name == "nt":
            return "${workspaceFolder}/.venv/Scripts/python", ["-m", "djobs.mcp_server"]
        return "${workspaceFolder}/.venv/bin/python", ["-m", "djobs.mcp_server"]

    console = shutil.which("djobs-mcp")
    if console:
        return console, []

    return sys.executable, ["-m", "djobs.mcp_server"]


def _cmd_install_mcp(args: argparse.Namespace) -> None:
    """Write .vscode/mcp.json (or print to stdout with --print)."""
    import json
    from pathlib import Path

    read_only = [
        "health",
        "resume_session",
        "check_task",
        "list_tasks",
        "audit_log",
    ]
    write_tools = ["enqueue_task", "complete_task", "fail_task"]
    approve_list = read_only + write_tools if args.full_approve else read_only

    cmd, cmd_args = _resolve_mcp_command(args)

    server: dict[str, Any] = {
        "type": "stdio",
        "command": cmd,
        "args": cmd_args,
        "autoApprove": approve_list,
    }

    # When a global/shared queue is requested, point the agent's MCP server at
    # the same database via the DJOBS_DB environment variable so reads (the VS
    # Code sidebar) and writes (the agent) share one queue.
    db = _global_db() if getattr(args, "use_global", False) else getattr(args, "db", None)
    if db:
        server["env"] = {"DJOBS_DB": str(Path(db).expanduser().resolve())}

    snippet = {"servers": {"djobs": server}}

    content = json.dumps(snippet, indent=2) + "\n"

    if args.print:
        print(content)
        return

    target = Path(args.output)
    if target.exists() and not args.force:
        print(f"Already exists: {target}")
        print("Use --force to overwrite, or --print to output to stdout.")
        sys.exit(1)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    mode = "full-approve" if args.full_approve else "read-only"
    print(f"Wrote {target}  (autoApprove: {mode})")
    if not args.full_approve:
        print(
            "Tip: rerun with --full-approve to include write tools "
            "(enqueue_task, complete_task, fail_task)."
        )

    if getattr(args, "write_instructions", True):
        _write_instructions_block()


# ---------------------------------------------------------------------------
# doctor command
# ---------------------------------------------------------------------------


def _probe_command(cmd: str) -> tuple[bool, str]:
    """Check whether an mcp.json launch command can actually be resolved."""
    import shutil
    from pathlib import Path

    if not cmd:
        return False, "empty command"
    if "${workspaceFolder}" in cmd:
        return True, "relocatable ${workspaceFolder} hint (resolved by VS Code at launch)"
    path = Path(cmd)
    if path.is_absolute():
        if path.exists():
            return True, "found"
        return False, "MISSING — interpreter/script not found"
    found = shutil.which(cmd)
    return (found is not None, found or "not found on PATH")


def _probe_db_writable(db_path: os.PathLike[str] | str) -> tuple[bool, str]:
    """Check whether the queue database can be opened/created without writing it."""
    import os as _os
    import sqlite3
    from pathlib import Path

    p = Path(db_path)
    try:
        if p.exists():
            con = sqlite3.connect(str(p))
            con.execute("PRAGMA user_version")
            con.close()
            return True, "exists, writable"
        parent = p.parent
        parent.mkdir(parents=True, exist_ok=True)
        if _os.access(parent, _os.W_OK):
            return True, "will be created on first use (parent writable)"
        return False, "parent directory not writable"
    except Exception as exc:
        return False, f"NOT usable: {exc}"


def _cmd_doctor(args: argparse.Namespace) -> None:
    """Diagnose the djobs setup and print a pass/fail checklist.

    Human mode exits non-zero when a critical check fails (useful in scripts);
    ``--json`` always exits 0 so callers can inspect the per-check flags.
    """
    import json
    import shutil
    from pathlib import Path

    checks: list[tuple[str, bool, str]] = []
    pkg_version: str | None = None

    # 1. djobs package importable
    try:
        import djobs as _djobs

        ver = getattr(_djobs, "__version__", "?")
        pkg_version = ver if ver != "?" else None
        file_attr = _djobs.__file__
        loc = Path(file_attr).resolve().parent if file_attr else "?"
        pkg_ok = True
        checks.append(("djobs package", True, f"v{ver} at {loc}"))
    except Exception as exc:
        pkg_ok = False
        checks.append(("djobs package", False, f"import failed: {exc}"))

    # 2. djobs-mcp console script on PATH (the global-tool wiring target)
    mcp_script = shutil.which("djobs-mcp")
    checks.append(
        (
            "djobs-mcp on PATH",
            mcp_script is not None,
            mcp_script or "not found — wiring falls back to the current interpreter (still works)",
        )
    )

    # 3. current interpreter (always informational)
    checks.append(("python interpreter", True, sys.executable))

    # 4. queue database location + writability
    env_db = os.environ.get("DJOBS_DB")
    db_path = Path(env_db).expanduser() if env_db else Path.home() / ".djobs" / "global.db"
    db_ok, db_detail = _probe_db_writable(db_path)
    db_label = f"queue db ({'DJOBS_DB' if env_db else 'global default'})"
    checks.append((db_label, db_ok, f"{db_path} — {db_detail}"))

    # 5. .vscode/mcp.json wiring in the current workspace
    mcp_json = Path(".vscode/mcp.json")
    if mcp_json.exists():
        try:
            data = json.loads(mcp_json.read_text(encoding="utf-8"))
            server = data.get("servers", {}).get("djobs", {})
            cmd = server.get("command", "")
            cmd_ok, cmd_detail = _probe_command(cmd)
            checks.append(("mcp.json wiring", cmd_ok, f"command={cmd!r} — {cmd_detail}"))
        except Exception as exc:
            checks.append(("mcp.json wiring", False, f"parse error: {exc}"))
    else:
        checks.append(
            ("mcp.json wiring", False, f"{mcp_json} not found — run 'djobs install-mcp'")
        )

    # 6. agent guidance block (checks every instruction target djobs manages)
    present_in: list[str] = []
    for rel in _INSTRUCTION_TARGETS.values():
        p = Path(rel)
        if p.exists() and _DJOBS_INSTRUCTIONS_START in p.read_text(encoding="utf-8"):
            present_in.append(rel)
    has_block = bool(present_in)
    checks.append(
        (
            "agent guidance block",
            has_block,
            f"present in {', '.join(present_in)}"
            if has_block
            else "missing — run 'djobs install-instructions'",
        )
    )

    # Informational checks are never failures: even when False, the setup still
    # works (e.g. no djobs-mcp on PATH just means wiring uses the interpreter
    # directly). Showing these as FAIL after a successful `djobs init` is exactly
    # what made the tool feel broken, so they render as INFO instead.
    info_checks = {"djobs-mcp on PATH"}

    if getattr(args, "as_json", False):
        print(
            json.dumps(
                {
                    "version": pkg_version,
                    "checks": [
                        {
                            "name": n,
                            "ok": o,
                            "detail": d,
                            "level": "info" if n in info_checks else "check",
                        }
                        for n, o, d in checks
                    ],
                },
                indent=2,
            )
        )
        return

    print("djobs doctor — setup diagnostics\n")
    for name, ok, detail in checks:
        if ok:
            mark = "OK  "
        elif name in info_checks:
            mark = "INFO"
        else:
            mark = "FAIL"
        print(f"  [{mark}] {name}: {detail}")
    print()

    # Critical = djobs importable AND the queue db is usable.
    if not (pkg_ok and db_ok):
        print("One or more critical checks failed. See the FAIL lines above.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# init command (one-command onboarding)
# ---------------------------------------------------------------------------


def _cmd_init(args: argparse.Namespace) -> None:
    """One-command setup: wire mcp.json, install agent instructions, run doctor.

    This is the recommended way to onboard a project. It is composed from the
    existing building blocks (``install-mcp`` + ``install-instructions`` +
    ``doctor``) so behaviour stays consistent and there is nothing new to learn.
    """
    # 1. MCP wiring. Reuse install-mcp, but don't abort the whole init when an
    #    mcp.json already exists — just keep it unless --force was given.
    mcp_target = Path(args.output)
    if mcp_target.exists() and not args.force:
        print(f"MCP wiring already present at {mcp_target} (use --force to rewrite).")
    else:
        mcp_args = argparse.Namespace(
            full_approve=args.full_approve,
            print=False,
            force=args.force,
            output=args.output,
            db=getattr(args, "db", None),
            use_global=args.use_global,
            python=args.python,
            command=args.command,
            portable=args.portable,
            # init writes instructions itself (below), per --instructions-target.
            write_instructions=False,
        )
        _cmd_install_mcp(mcp_args)

    # 2. Agent instructions.
    for target in _resolve_instruction_targets(args.instructions_target):
        _write_instructions_to(target)

    # 3. Diagnostics. doctor exits non-zero on a critical failure, which also
    #    suppresses the success message below — intentional, so we never claim
    #    success on a broken setup.
    print()
    _cmd_doctor(argparse.Namespace(as_json=False))

    # 4. Success + next steps.
    print(
        "\ndjobs is initialized.\n\n"
        "Next steps:\n"
        "1. Restart VS Code / your agent host so it reloads .vscode/mcp.json.\n"
        "2. Start a new agent session.\n"
        "3. Ask the agent to call resume_session before continuing long-running work."
    )


# ---------------------------------------------------------------------------
# audit command
# ---------------------------------------------------------------------------


def _cmd_audit(args: argparse.Namespace) -> None:
    """Query the audit trail from the terminal."""
    import json
    from collections import Counter
    from datetime import UTC, datetime, timedelta

    from djobs.storage.sqlite import SQLiteJobRepository

    repo = SQLiteJobRepository.from_path(args.db)

    now = datetime.now(UTC)
    if args.since:
        try:
            since_dt = datetime.fromisoformat(args.since)
        except ValueError:
            print(f"Error: invalid --since datetime: {args.since}")
            sys.exit(1)
    else:
        since_dt = now - timedelta(hours=24)
    if since_dt.tzinfo is None:
        since_dt = since_dt.replace(tzinfo=UTC)

    where_clauses = ["e.created_at >= ?"]
    params: list[Any] = [since_dt.isoformat()]

    if args.correlation_id:
        where_clauses.append("j.correlation_id = ?")
        params.append(args.correlation_id)
    if args.failures:
        where_clauses.append("e.event_type = ?")
        params.append("job_failed")

    where_sql = " AND ".join(where_clauses)
    sql = f"""
        SELECT
            e.event_type, e.message, e.created_at AS event_at,
            j.type AS task_type, j.status AS job_status,
            j.correlation_id, j.id AS job_id
        FROM job_events e
        JOIN jobs j ON e.job_id = j.id
        WHERE {where_sql}
        ORDER BY e.created_at DESC
        LIMIT ?
    """

    with repo._lock:
        rows = repo._connection.execute(sql, (*params, args.limit)).fetchall()

    if args.detail or args.output_format == "json":
        events = [dict(row) for row in rows]
        if args.output_format == "json":
            print(json.dumps(events, indent=2, default=str))
        else:
            for ev in events:
                ts = ev["event_at"][:19] if ev["event_at"] else "?"
                print(f"  {ts}  {ev['event_type']:20s}  {ev['task_type']:20s}  {ev['job_id'][:8]}")
    else:
        # Summary mode
        type_counts: Counter[str] = Counter()
        status_counts: Counter[str] = Counter()
        failures: list[dict[str, Any]] = []
        for row in rows:
            type_counts[row["event_type"]] += 1
            status_counts[row["job_status"]] += 1
            if row["event_type"] == "job_failed" and len(failures) < 5:
                failures.append(dict(row))

        print(f"Audit summary (since {since_dt.isoformat()[:19]}Z):\n")
        print("Events by type:")
        for k, v in type_counts.most_common():
            print(f"  {k:30s} {v}")
        print("\nJobs by current status:")
        for k, v in status_counts.most_common():
            print(f"  {k:30s} {v}")
        if failures:
            print(f"\nRecent failures ({len(failures)}):")
            for f in failures:
                print(f"  {f['job_id'][:8]}  {f['task_type']}  {f['message']}")
        print(f"\nTotal events scanned: {len(rows)}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        prog="djobs",
        description="Distributed job system CLI",
    )
    subparsers = parser.add_subparsers(dest="subcommand")

    # --- serve ---
    serve_parser = subparsers.add_parser(
        "serve",
        help="Start the background daemon",
    )
    serve_parser.add_argument(
        "--db",
        default=None,
        help="SQLite database path (default: $DJOBS_DB or djobs_mcp.db — same as MCP server)",
    )
    serve_parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Max concurrent workers (default: 4)",
    )
    serve_parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="Seconds between job claim attempts (default: 1.0)",
    )
    serve_parser.add_argument(
        "--scheduler-interval",
        type=float,
        default=5.0,
        help="Seconds between scheduler ticks (default: 5.0)",
    )
    serve_parser.add_argument(
        "--handlers",
        nargs="*",
        metavar="MODULE",
        help=(
            "Python modules exporting a HANDLERS dict. "
            "E.g. --handlers myapp.handlers myapp.extra_handlers"
        ),
    )
    serve_parser.set_defaults(func=_cmd_serve)

    # --- mcp ---
    mcp_serve_parser = subparsers.add_parser(
        "mcp",
        help="Run the MCP server over stdio (for agents / uvx; honors DJOBS_DB)",
    )
    mcp_serve_parser.add_argument(
        "--db",
        default=None,
        help="SQLite database path (default: $DJOBS_DB or djobs_mcp.db)",
    )
    mcp_serve_parser.set_defaults(func=_cmd_mcp)

    # --- dashboard ---
    dashboard_parser = subparsers.add_parser(
        "dashboard",
        help="Serve the read-only cross-agent web dashboard",
    )
    dashboard_parser.add_argument(
        "--db",
        default=None,
        help="SQLite database path (default: $DJOBS_DB or djobs_mcp.db — same as MCP server)",
    )
    dashboard_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help=(
            "Host/interface to bind (default: 127.0.0.1). The dashboard has NO "
            "authentication; only bind to a non-loopback address (e.g. 0.0.0.0) "
            "on a trusted network."
        ),
    )
    dashboard_parser.add_argument(
        "--port",
        type=int,
        default=8787,
        help="Port to listen on (default: 8787)",
    )
    dashboard_parser.add_argument(
        "--refresh",
        type=int,
        default=5,
        help="Auto-refresh interval in seconds (default: 5)",
    )
    dashboard_parser.set_defaults(func=_cmd_dashboard)

    # --- status (JSON) ---
    status_parser = subparsers.add_parser(
        "status",
        help="JSON snapshot of queue health + tasks (for VS Code extension)",
    )
    status_parser.add_argument(
        "--db",
        default=None,
        help="SQLite database path (default: $DJOBS_DB or djobs_mcp.db)",
    )
    status_parser.add_argument(
        "--correlation-id",
        default=None,
        help="Filter tasks by correlation_id",
    )
    status_parser.set_defaults(func=_cmd_status)

    # --- skip ---
    skip_parser = subparsers.add_parser(
        "skip",
        help="Mark a task as intentionally skipped/accepted",
    )
    skip_parser.add_argument("job_id", help="Task/job ID to mark as skipped")
    skip_parser.add_argument("--db", default=None, help="SQLite database path")
    skip_parser.add_argument(
        "--evidence",
        default="Skipped by user/operator",
        help="Evidence/note stored on the completed task",
    )
    skip_parser.set_defaults(func=_cmd_skip)

    # --- accept-before ---
    accept_before_parser = subparsers.add_parser(
        "accept-before",
        help="Mark all earlier tasks in the same workflow as accepted",
    )
    accept_before_parser.add_argument("job_id", help="Start task/job ID")
    accept_before_parser.add_argument("--db", default=None, help="SQLite database path")
    accept_before_parser.add_argument(
        "--evidence",
        default=None,
        help="Evidence/note stored on accepted tasks",
    )
    accept_before_parser.set_defaults(func=_cmd_accept_before)

    # --- archive-workflow ---
    archive_workflow_parser = subparsers.add_parser(
        "archive-workflow",
        help="Archive all non-terminal tasks in a workflow/session",
    )
    archive_workflow_parser.add_argument(
        "--db",
        default=None,
        help="SQLite database path",
    )
    archive_workflow_parser.add_argument(
        "--correlation-id",
        default=None,
        help="Workflow/session correlation_id to archive (omit to archive all non-terminal tasks)",
    )
    archive_workflow_parser.add_argument(
        "--reason",
        default="Archived by user/operator",
        help="Reason recorded in the audit log",
    )
    archive_workflow_parser.set_defaults(func=_cmd_archive_workflow)

    # --- explain ---
    explain_parser = subparsers.add_parser(
        "explain",
        help="Explain in plain language why each still-visible task is in the queue",
    )
    explain_parser.add_argument(
        "--db",
        default=None,
        help="SQLite database path (default: $DJOBS_DB or djobs_mcp.db)",
    )
    explain_parser.add_argument(
        "--correlation-id",
        default=None,
        help="Only explain tasks in this workflow/session (omit for all visible tasks)",
    )
    explain_parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        dest="output_format",
        help="Output format (default: table)",
    )
    explain_parser.set_defaults(func=_cmd_explain)

    # --- token-savings ---
    token_savings_parser = subparsers.add_parser(
        "token-savings",
        help="Estimate replay tokens saved by durable completed-task evidence",
    )
    token_savings_parser.add_argument(
        "--db",
        default=None,
        help="SQLite database path (default: $DJOBS_DB or djobs_mcp.db)",
    )
    token_savings_parser.add_argument(
        "--correlation-id",
        default=None,
        help="Only estimate one workflow/session (omit for all non-archived tasks)",
    )
    token_savings_parser.add_argument(
        "--redo-overhead-tokens",
        type=int,
        default=600,
        help="Estimated re-read/re-plan overhead per completed task (default: 600)",
    )
    token_savings_parser.add_argument(
        "--chars-per-token",
        type=float,
        default=4.0,
        help="Approximate characters per token for estimation (default: 4.0)",
    )
    token_savings_parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        dest="output_format",
        help="Output format (default: table)",
    )
    token_savings_parser.set_defaults(func=_cmd_token_savings)

    # --- install-mcp ---
    mcp_parser = subparsers.add_parser(
        "install-mcp",
        help="Write .vscode/mcp.json for VS Code (or --print to stdout)",
    )
    mcp_parser.add_argument(
        "--full-approve",
        action="store_true",
        help="Include write tools (enqueue_task, complete_task, fail_task) in autoApprove",
    )
    mcp_parser.add_argument(
        "--print",
        action="store_true",
        help="Print to stdout instead of writing a file",
    )
    mcp_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing mcp.json without asking",
    )
    mcp_parser.add_argument(
        "-o",
        "--output",
        default=".vscode/mcp.json",
        help="Output path (default: .vscode/mcp.json)",
    )
    mcp_parser.add_argument(
        "--python",
        default=None,
        help=(
            "Python interpreter the MCP server runs under "
            "(launches '<python> -m djobs.mcp_server'). Default: the 'djobs-mcp' "
            "console script if on PATH, otherwise the current interpreter."
        ),
    )
    mcp_parser.add_argument(
        "--command",
        default=None,
        help=(
            "Exact launch command for the MCP server (e.g. 'djobs-mcp'). "
            "Overrides --python and --portable."
        ),
    )
    mcp_parser.add_argument(
        "--portable",
        action="store_true",
        help=(
            "Emit a relocatable '${workspaceFolder}/.venv' interpreter hint "
            "instead of an absolute path. Use when committing mcp.json to a repo "
            "whose collaborators each keep djobs in a project-local venv."
        ),
    )
    mcp_parser.add_argument(
        "--db",
        default=None,
        help=(
            "Point the agent's MCP server at this database via DJOBS_DB. "
            "Use for a shared/global queue. Omit for the workspace-local default."
        ),
    )
    mcp_parser.add_argument(
        "--global",
        dest="use_global",
        action="store_true",
        help="Use the shared global queue at ~/.djobs/global.db (sets --db for you).",
    )
    mcp_parser.add_argument(
        "--no-instructions",
        dest="write_instructions",
        action="store_false",
        help=(
            "Do NOT add the djobs guidance block to .github/copilot-instructions.md. "
            "By default a managed block is appended so the AI agent uses djobs proactively."
        ),
    )
    mcp_parser.set_defaults(func=_cmd_install_mcp, write_instructions=True)

    # --- install-instructions ---
    instr_parser = subparsers.add_parser(
        "install-instructions",
        help="Create/update the djobs agent guidance block (does NOT touch mcp.json)",
    )
    instr_parser.add_argument(
        "--target",
        choices=["copilot", "agent-md", "all"],
        default="copilot",
        help=(
            "Which instruction file(s) to write: copilot=.github/copilot-instructions.md "
            "(default), agent-md=.agent.md, all=both."
        ),
    )
    instr_parser.add_argument(
        "--print",
        action="store_true",
        help="Print the managed block to stdout instead of writing files",
    )
    instr_parser.set_defaults(func=_cmd_install_instructions)

    # --- init (one-command onboarding) ---
    init_parser = subparsers.add_parser(
        "init",
        help="One-command setup: wire mcp.json + install agent instructions + run doctor",
    )
    init_parser.add_argument(
        "--full-approve",
        action="store_true",
        help="Include write tools (enqueue_task, complete_task, fail_task) in autoApprove",
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing .vscode/mcp.json",
    )
    init_parser.add_argument(
        "-o",
        "--output",
        default=".vscode/mcp.json",
        help="mcp.json output path (default: .vscode/mcp.json)",
    )
    init_parser.add_argument(
        "--python",
        default=None,
        help="Python interpreter the MCP server runs under (see 'install-mcp --python').",
    )
    init_parser.add_argument(
        "--command",
        default=None,
        help="Exact launch command for the MCP server (see 'install-mcp --command').",
    )
    init_parser.add_argument(
        "--portable",
        action="store_true",
        help="Emit a relocatable '${workspaceFolder}/.venv' hint (see 'install-mcp --portable').",
    )
    init_parser.add_argument(
        "--db",
        default=None,
        help="Point the agent's MCP server at this database via DJOBS_DB.",
    )
    init_parser.add_argument(
        "--global",
        dest="use_global",
        action="store_true",
        help="Use the shared global queue at ~/.djobs/global.db (sets --db for you).",
    )
    init_parser.add_argument(
        "--instructions-target",
        choices=["copilot", "agent-md", "all"],
        default="copilot",
        help=(
            "Which instruction file(s) to write: copilot=.github/copilot-instructions.md "
            "(default), agent-md=.agent.md, all=both."
        ),
    )
    init_parser.set_defaults(func=_cmd_init)

    # --- doctor ---
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Diagnose djobs setup (interpreter, wiring, db) and print a checklist",
    )
    doctor_parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Machine-readable JSON output (always exits 0; inspect per-check 'ok' flags)",
    )
    doctor_parser.set_defaults(func=_cmd_doctor)

    # --- audit ---
    audit_parser = subparsers.add_parser(
        "audit",
        help="Query the audit trail",
    )
    audit_parser.add_argument(
        "--db",
        default=None,
        help="SQLite database path (default: $DJOBS_DB or djobs_mcp.db)",
    )
    audit_parser.add_argument(
        "--since",
        default=None,
        help="ISO datetime lower bound (default: last 24h)",
    )
    audit_parser.add_argument(
        "--correlation-id",
        default=None,
        help="Filter by correlation_id",
    )
    audit_parser.add_argument(
        "--failures",
        action="store_true",
        help="Show only failures",
    )
    audit_parser.add_argument(
        "--detail",
        action="store_true",
        help="Show individual events instead of summary",
    )
    audit_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Max events to return (default: 100)",
    )
    audit_parser.add_argument(
        "--format",
        choices=["json", "table"],
        default="table",
        dest="output_format",
        help="Output format (default: table)",
    )
    audit_parser.set_defaults(func=_cmd_audit)

    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)
    # install-mcp / init manage their own db wiring (DJOBS_DB env); don't auto-resolve.
    # `mcp` likewise honors DJOBS_DB itself (and only configures when --db is given).
    if (
        args.subcommand not in ("install-mcp", "init", "mcp")
        and getattr(args, "db", None) is None
        and hasattr(args, "db")
    ):
        args.db = _default_db()
    args.func(args)


if __name__ == "__main__":
    main()
