"""CLI entry point for the distributed job system.

Commands::

    djobs serve              Start the background daemon
    djobs serve --db my.db   Use a custom database path
    djobs serve --workers 8  Set max concurrent workers
    djobs install-mcp        Print an mcp.json snippet for VS Code
    djobs audit              Query the audit trail from the terminal
"""

from __future__ import annotations

import argparse
import importlib
import logging
import sys
from collections.abc import Callable
from typing import Any

Handler = Callable[[dict[str, Any]], Any]


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
# install-mcp command
# ---------------------------------------------------------------------------


def _cmd_install_mcp(args: argparse.Namespace) -> None:
    """Write .vscode/mcp.json (or print to stdout with --print)."""
    import json
    import os
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

    # Detect OS-appropriate python path hint
    if os.name == "nt":
        cmd = "${workspaceFolder}/.venv/Scripts/python"
    else:
        cmd = "${workspaceFolder}/.venv/bin/python"

    snippet = {
        "servers": {
            "djobs": {
                "type": "stdio",
                "command": cmd,
                "args": ["-m", "djobs.mcp_server"],
                "autoApprove": approve_list,
            }
        }
    }

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
        default="djobs_mcp.db",
        help="SQLite database path (default: djobs_mcp.db — same as MCP server)",
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
    mcp_parser.set_defaults(func=_cmd_install_mcp)

    # --- audit ---
    audit_parser = subparsers.add_parser(
        "audit",
        help="Query the audit trail",
    )
    audit_parser.add_argument(
        "--db",
        default="djobs_mcp.db",
        help="SQLite database path (default: djobs_mcp.db)",
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
    args.func(args)


if __name__ == "__main__":
    main()
