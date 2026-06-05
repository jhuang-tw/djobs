"""CLI entry point for the distributed job system.

Commands::

    djobs serve              Start the background daemon
    djobs serve --db my.db   Use a custom database path
    djobs serve --workers 8  Set max concurrent workers
    djobs dashboard          Serve the read-only cross-agent web dashboard
    djobs install-mcp        Print an mcp.json snippet for VS Code
    djobs doctor             Diagnose setup (interpreter, wiring, db)
    djobs audit              Query the audit trail from the terminal
"""

from __future__ import annotations

import argparse
import importlib
import logging
import os
import sys
from collections.abc import Callable
from typing import Any

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


def _cmd_status(args: argparse.Namespace) -> None:
    """JSON snapshot for the VS Code extension."""
    import json
    from datetime import UTC, datetime

    from djobs.storage.sqlite import SQLiteJobRepository

    repo = SQLiteJobRepository.from_path(args.db)
    q_module = __import__("djobs.queue.service", fromlist=["QueueService"])
    queue = q_module.QueueService(repo)

    health_data = queue.health()

    evidence_by_job: dict[str, str | None] = {}
    with repo._lock:
        event_rows = repo._connection.execute(
            """
            SELECT job_id, message
            FROM job_events
            WHERE event_type = 'job_succeeded'
            ORDER BY created_at DESC
            """
        ).fetchall()
    for row in event_rows:
        evidence_by_job.setdefault(row["job_id"], row["message"])

    tasks: list[dict[str, Any]] = []
    with repo._lock:
        if args.correlation_id:
            rows = repo._connection.execute(
                "SELECT id, type, status, payload_json, correlation_id, "
                "created_at, updated_at, attempt, max_attempts, last_error, "
                "depends_on_json "
                "FROM jobs WHERE correlation_id = ? AND status != ? ORDER BY created_at",
                (args.correlation_id, "archived"),
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

- **Resume first.** At the start of a session that continues prior work, call
  `resume_session` (with the session/correlation_id) to recover any unfinished
  tasks before starting something new.
- **Enqueue long or risky work.** When a request is long-running, multi-step,
  or could be interrupted (large refactors, batch edits, migrations, builds,
  data processing), submit it with `enqueue_task` and a stable `correlation_id`
  so progress is durable and visible in the djobs sidebar. Mark each unit done
  with `complete_task` / `fail_task`.
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


def _write_instructions_block() -> None:
    """Append/update the djobs managed block in .github/copilot-instructions.md.

    Uses sentinel markers so re-running only updates the djobs block and never
    touches the user's other instructions.
    """
    from pathlib import Path

    target = Path(".github/copilot-instructions.md")
    block = f"{_DJOBS_INSTRUCTIONS_START}\n{_DJOBS_INSTRUCTIONS_BODY}{_DJOBS_INSTRUCTIONS_END}\n"

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

    # 6. agent guidance block
    instr = Path(".github/copilot-instructions.md")
    has_block = False
    if instr.exists():
        has_block = _DJOBS_INSTRUCTIONS_START in instr.read_text(encoding="utf-8")
    checks.append(
        (
            "agent guidance block",
            has_block,
            "present" if has_block else "missing — agent may not use djobs proactively",
        )
    )

    if getattr(args, "as_json", False):
        print(
            json.dumps(
                {
                    "version": pkg_version,
                    "checks": [{"name": n, "ok": o, "detail": d} for n, o, d in checks],
                },
                indent=2,
            )
        )
        return

    print("djobs doctor — setup diagnostics\n")
    for name, ok, detail in checks:
        mark = "OK  " if ok else "FAIL"
        print(f"  [{mark}] {name}: {detail}")
    print()

    # Critical = djobs importable AND the queue db is usable.
    if not (pkg_ok and db_ok):
        print("One or more critical checks failed. See the FAIL lines above.")
        sys.exit(1)


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
    subparsers = parser.add_subparsers(dest="command")

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
    # install-mcp manages its own db wiring (DJOBS_DB env); don't auto-resolve.
    if args.command != "install-mcp" and getattr(args, "db", None) is None and hasattr(args, "db"):
        args.db = _default_db()
    args.func(args)


if __name__ == "__main__":
    main()
