"""Explainable heuristic context comparisons for durable djobs state."""

from __future__ import annotations

import argparse
import json
import math
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from djobs.core.correlation import correlation_id_variants
from djobs.storage.sqlite import SQLiteJobRepository

DEFAULT_CHARS_PER_TOKEN = 4.0
DEFAULT_REDO_OVERHEAD_TOKENS = 600
_HISTORY_LIMIT = 20


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        result = datetime.fromisoformat(text)
    except ValueError:
        return None
    return (
        result.replace(tzinfo=timezone.utc)
        if result.tzinfo is None
        else result.astimezone(timezone.utc)
    )


def _estimate_tokens(text: str, chars_per_token: float) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / chars_per_token))


def _load_payload(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _task_label(job_type: str, payload: dict[str, Any]) -> str:
    if job_type == "auto-command":
        command = payload.get("command") or payload.get("summary") or "automatic command"
        if isinstance(command, str):
            words = command.split()
            label = " ".join(words[:3])
            return f"{label} ..." if len(words) > 3 else label
    for key in ("summary", "title", "name", "description", "file", "path", "why"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return job_type


def _payload_summary(job_type: str, payload: dict[str, Any]) -> str:
    fields: list[str] = []
    for key in (
        "summary",
        "title",
        "name",
        "description",
        "file",
        "path",
        "why",
        "condition",
    ):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            fields.append(value.strip())
    if job_type == "auto-command":
        command = payload.get("command")
        if isinstance(command, str) and command.strip():
            fields.append(command.strip())
    return " | ".join(fields)


def _latest_success_events(repo: SQLiteJobRepository) -> dict[str, dict[str, Any]]:
    with repo._lock:
        rows = repo._connection.execute(
            """
            SELECT job_id, message, metadata_json, created_at
            FROM job_events
            WHERE event_type = 'job_succeeded'
            ORDER BY created_at ASC, rowid ASC
            """
        ).fetchall()

    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        evidence = row["message"] or ""
        if not evidence and row["metadata_json"]:
            try:
                metadata = json.loads(row["metadata_json"])
            except json.JSONDecodeError:
                metadata = {}
            if isinstance(metadata, dict) and isinstance(metadata.get("evidence"), str):
                evidence = metadata["evidence"]
        result[row["job_id"]] = {
            "evidence": evidence,
            "created_at": row["created_at"],
        }
    return result


def _query_jobs(
    repo: SQLiteJobRepository,
    correlation_id: str | None,
) -> list[Any]:
    columns = (
        "id, type, status, payload_json, correlation_id, last_error, "
        "attempt, max_attempts, started_at, created_at, updated_at"
    )
    with repo._lock:
        if correlation_id is None:
            return list(
                repo._connection.execute(
                    f"SELECT {columns} FROM jobs ORDER BY created_at ASC, rowid ASC"
                ).fetchall()
            )
        variants = correlation_id_variants(correlation_id)
        placeholders = ",".join("?" for _ in variants)
        return list(
            repo._connection.execute(
                f"SELECT {columns} FROM jobs "
                f"WHERE correlation_id IN ({placeholders}) "
                "ORDER BY created_at ASC, rowid ASC",
                tuple(variants),
            ).fetchall()
        )


def _record_from_row(
    row: Any,
    success: dict[str, Any] | None,
    *,
    chars_per_token: float,
    redo_overhead_tokens: int,
) -> dict[str, Any]:
    payload = _load_payload(row["payload_json"])
    source = "automatic_hook" if row["type"] == "auto-command" else "durable_workflow"
    completed = success is not None or row["status"] == "succeeded"
    evidence = success["evidence"] if success is not None else ""
    summary = _payload_summary(row["type"], payload)
    durable_text = "\n".join(
        part
        for part in (
            row["type"],
            row["status"],
            summary,
            evidence,
            row["last_error"] or "",
        )
        if part
    )
    replay_text = "\n".join(part for part in (row["type"], summary, evidence) if part)
    durable_tokens = _estimate_tokens(durable_text, chars_per_token)
    replay_tokens = (
        redo_overhead_tokens + _estimate_tokens(replay_text, chars_per_token) if completed else 0
    )
    saved_tokens = max(0, replay_tokens - durable_tokens) if completed else 0
    event_at = (
        _parse_dt(success["created_at"])
        if success is not None
        else _parse_dt(row["updated_at"]) or _parse_dt(row["created_at"])
    )
    created_at = _parse_dt(row["created_at"])
    cycle_seconds = (
        max(0.0, (event_at - created_at).total_seconds())
        if completed and event_at is not None and created_at is not None
        else None
    )
    attempts = max(1, int(row["attempt"] or 0)) if completed else int(row["attempt"] or 0)
    repair_attempts = max(0, attempts - 1) if completed else 0
    return {
        "id": row["id"],
        "type": row["type"],
        "source": source,
        "status": row["status"],
        "correlation_id": row["correlation_id"],
        "label": _task_label(row["type"], payload),
        "completed": completed,
        "event_at": event_at,
        "attempts": attempts,
        "repair_attempts": repair_attempts,
        "first_pass_verified": completed and repair_attempts == 0,
        "cycle_seconds": cycle_seconds,
        "estimated_without_djobs_tokens": replay_tokens,
        "estimated_with_djobs_tokens": durable_tokens if completed else 0,
        "estimated_saved_tokens": saved_tokens,
        "protected_context_tokens": durable_tokens,
    }


def _verified_task_efficiency(records: list[dict[str, Any]]) -> dict[str, Any]:
    verified = [
        record
        for record in records
        if record["completed"] and record["source"] == "durable_workflow"
    ]
    count = len(verified)
    first_pass = sum(1 for record in verified if record["first_pass_verified"])
    total_attempts = sum(int(record["attempts"]) for record in verified)
    repair_attempts = sum(int(record["repair_attempts"]) for record in verified)
    cycle_values = [
        float(record["cycle_seconds"])
        for record in verified
        if record["cycle_seconds"] is not None
    ]
    context_tokens = sum(int(record["estimated_with_djobs_tokens"]) for record in verified)
    saved_tokens = sum(int(record["estimated_saved_tokens"]) for record in verified)
    return {
        "verified_tasks": count,
        "first_pass_verified_tasks": first_pass,
        "first_pass_verified_percent": round((first_pass / count) * 100, 1) if count else 0.0,
        "total_attempts": total_attempts,
        "repair_attempts": repair_attempts,
        "average_attempts_per_verified_task": round(total_attempts / count, 2) if count else 0.0,
        "average_cycle_seconds": round(sum(cycle_values) / len(cycle_values), 3)
        if cycle_values
        else 0.0,
        "cost_per_verified_task": {
            "estimated_context_tokens": round(context_tokens / count, 1) if count else 0.0,
            "estimated_saved_tokens": round(saved_tokens / count, 1) if count else 0.0,
            "average_attempts": round(total_attempts / count, 2) if count else 0.0,
            "average_cycle_seconds": round(sum(cycle_values) / len(cycle_values), 3)
            if cycle_values
            else 0.0,
        },
    }


def _summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [record for record in records if record["completed"]]
    without = sum(record["estimated_without_djobs_tokens"] for record in completed)
    with_djobs = sum(record["estimated_with_djobs_tokens"] for record in completed)
    saved = sum(record["estimated_saved_tokens"] for record in completed)
    percent = round((saved / without) * 100, 1) if without else 0.0
    sources: dict[str, dict[str, int]] = {}
    for source in ("automatic_hook", "durable_workflow"):
        selected = [record for record in completed if record["source"] == source]
        sources[source] = {
            "completed_records": len(selected),
            "estimated_saved_tokens": sum(record["estimated_saved_tokens"] for record in selected),
        }
    return {
        "completed_records": len(completed),
        "estimated_without_djobs_tokens": without,
        "estimated_with_djobs_tokens": with_djobs,
        "estimated_saved_tokens": saved,
        "estimated_saved_percent": percent,
        "verified_task_efficiency": _verified_task_efficiency(completed),
        "sources": sources,
    }


def build_gain_report(
    db_path: str | Path,
    correlation_id: str | None,
    *,
    now: datetime | None = None,
    chars_per_token: float = DEFAULT_CHARS_PER_TOKEN,
    redo_overhead_tokens: int = DEFAULT_REDO_OVERHEAD_TOKENS,
    history_limit: int = _HISTORY_LIMIT,
) -> dict[str, Any]:
    """Build an explainable heuristic comparison without mutating durable state."""

    if chars_per_token <= 0:
        raise ValueError("chars_per_token must be greater than 0")
    if redo_overhead_tokens < 0:
        raise ValueError("redo_overhead_tokens must be 0 or greater")

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    repo = SQLiteJobRepository.from_path(db_path)
    success_by_job = _latest_success_events(repo)
    records = [
        _record_from_row(
            row,
            success_by_job.get(row["id"]),
            chars_per_token=chars_per_token,
            redo_overhead_tokens=redo_overhead_tokens,
        )
        for row in _query_jobs(repo, correlation_id)
    ]

    def since(delta: timedelta) -> list[dict[str, Any]]:
        cutoff = current - delta
        return [
            record
            for record in records
            if record["event_at"] is not None and record["event_at"] >= cutoff
        ]

    recoverable = [
        record
        for record in records
        if not record["completed"]
        and record["status"]
        in {"pending", "running", "retry_scheduled", "failed", "dead_lettered"}
    ]
    status_counts = Counter(record["status"] for record in recoverable)

    daily_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    cutoff_day = (current - timedelta(days=29)).date()
    for record in records:
        event_at = record["event_at"]
        if event_at is None or event_at.date() < cutoff_day:
            continue
        daily_map[event_at.date().isoformat()].append(record)
    daily: list[dict[str, Any]] = []
    for offset in range(29, -1, -1):
        day = (current.date() - timedelta(days=offset)).isoformat()
        summary = _summarize(daily_map.get(day, []))
        daily.append({"date": day, **summary})

    history = sorted(
        records,
        key=lambda record: record["event_at"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )[:history_limit]
    history_output = [
        {
            **{key: value for key, value in record.items() if key != "event_at"},
            "event_at": record["event_at"].isoformat() if record["event_at"] else None,
        }
        for record in history
    ]

    return {
        "generated_at": current.isoformat(),
        "scope": correlation_id or "all workspaces",
        "database": str(Path(db_path).expanduser()),
        "estimate_kind": "simple replay baseline versus stored durable context",
        "assumptions": {
            "chars_per_token": chars_per_token,
            "redo_overhead_tokens_per_completed_record": redo_overhead_tokens,
            "note": (
                "Estimates compare a configurable replay baseline with compact durable state. "
                "Modern agents may summarize, cache, or selectively reread, so the baseline can "
                "overstate avoided work. This is not observed provider usage or billing data."
            ),
        },
        "last_24_hours": _summarize(since(timedelta(hours=24))),
        "last_30_days": _summarize(since(timedelta(days=30))),
        "all_time": _summarize(records),
        "recoverable": {
            "checkpoints": len(recoverable),
            "protected_context_tokens": sum(
                record["protected_context_tokens"] for record in recoverable
            ),
            "by_status": dict(status_counts),
        },
        "daily": daily,
        "history": history_output,
    }


def _format_tokens(value: int) -> str:
    return f"{value:,}"


def _print_window(label: str, data: dict[str, Any]) -> None:
    print(
        f"  {label:<9} {_format_tokens(data['estimated_saved_tokens']):>10} heuristic delta  "
        f"({data['completed_records']} completed records)"
    )


def _print_graph(daily: list[dict[str, Any]]) -> None:
    nonzero = [day["estimated_saved_tokens"] for day in daily]
    maximum = max(nonzero, default=0)
    print("\nLast 30 days")
    for day in daily:
        value = day["estimated_saved_tokens"]
        width = 0 if maximum == 0 else max(1, round((value / maximum) * 28))
        bar = "#" * width
        print(f"  {day['date'][5:]}  {bar:<28} {_format_tokens(value):>8}")


def _print_daily(daily: list[dict[str, Any]]) -> None:
    print("\nDaily breakdown")
    for day in daily:
        if day["completed_records"] == 0:
            continue
        print(
            f"  {day['date']}  {_format_tokens(day['estimated_saved_tokens']):>9} saved  "
            f"{day['completed_records']:>3} completed"
        )


def _print_history(history: list[dict[str, Any]]) -> None:
    print("\nRecent savings history")
    if not history:
        print("  No durable records yet.")
        return
    for record in history:
        timestamp = (record["event_at"] or "?")[:19]
        saved = record["estimated_saved_tokens"]
        print(
            f"  {timestamp}  {record['source']:<16} "
            f"{_format_tokens(saved):>8}  {record['status']:<13} {record['label']}"
        )


def print_gain_report(
    report: dict[str, Any],
    *,
    show_history: bool = False,
    show_daily: bool = False,
    show_graph: bool = False,
) -> None:
    print("djobs gain - heuristic durable-context comparison")
    print(f"  scope: {report['scope']}")
    _print_window("24 hours", report["last_24_hours"])
    _print_window("30 days", report["last_30_days"])
    _print_window("all time", report["all_time"])

    all_time = report["all_time"]
    auto = all_time["sources"]["automatic_hook"]
    workflow = all_time["sources"]["durable_workflow"]
    print("\nEstimated delta sources")
    print(
        f"  automatic hooks   {_format_tokens(auto['estimated_saved_tokens']):>10} "
        f"({auto['completed_records']} completed commands)"
    )
    print(
        f"  durable workflows {_format_tokens(workflow['estimated_saved_tokens']):>10} "
        f"({workflow['completed_records']} completed tasks)"
    )

    efficiency = all_time["verified_task_efficiency"]
    cost = efficiency["cost_per_verified_task"]
    print("\nVerified task efficiency")
    print(
        f"  first-pass verified {efficiency['first_pass_verified_tasks']}/"
        f"{efficiency['verified_tasks']} ({efficiency['first_pass_verified_percent']:.1f}%)"
    )
    print(
        f"  repair attempts     {efficiency['repair_attempts']} total, "
        f"{efficiency['average_attempts_per_verified_task']:.2f} attempts/task"
    )
    print(
        f"  cost per task       {cost['estimated_context_tokens']:.1f} context tokens, "
        f"{cost['average_cycle_seconds']:.3f}s cycle proxy"
    )

    recoverable = report["recoverable"]
    print("\nRecovery state")
    print(
        f"  {recoverable['checkpoints']} unfinished/failed checkpoint(s), "
        f"{_format_tokens(recoverable['protected_context_tokens'])} tokens of compact "
        "context protected"
    )
    print(
        "\n  Heuristic only: modern agents may summarize or selectively reread; "
        "not observed provider usage or guaranteed savings."
    )

    if show_graph:
        _print_graph(report["daily"])
    if show_daily:
        _print_daily(report["daily"])
    if show_history:
        _print_history(report["history"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="djobs gain",
        description="Show explainable local context heuristics from durable djobs state",
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("DJOBS_DB") or "djobs_mcp.db",
        help="SQLite database path (default: $DJOBS_DB or djobs_mcp.db)",
    )
    scope_group = parser.add_mutually_exclusive_group()
    scope_group.add_argument(
        "--all",
        dest="all_workspaces",
        action="store_true",
        help="Aggregate every workspace in the selected database",
    )
    scope_group.add_argument(
        "--correlation-id",
        default=None,
        help="Report one explicit workflow/workspace correlation id",
    )
    parser.add_argument("--history", action="store_true", help="Show recent records")
    parser.add_argument("--daily", action="store_true", help="Show non-empty daily totals")
    parser.add_argument("--graph", action="store_true", help="Show a 30-day ASCII graph")
    parser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        dest="output_format",
    )
    parser.add_argument(
        "--redo-overhead-tokens",
        type=int,
        default=DEFAULT_REDO_OVERHEAD_TOKENS,
        help="Estimated re-read/re-plan overhead per completed record (default: 600)",
    )
    parser.add_argument(
        "--chars-per-token",
        type=float,
        default=DEFAULT_CHARS_PER_TOKEN,
        help="Approximate characters per token (default: 4.0)",
    )
    args = parser.parse_args(argv)

    correlation_id = None
    if not args.all_workspaces:
        correlation_id = args.correlation_id or os.getcwd()

    try:
        report = build_gain_report(
            args.db,
            correlation_id,
            chars_per_token=args.chars_per_token,
            redo_overhead_tokens=args.redo_overhead_tokens,
        )
    except ValueError as exc:
        parser.error(str(exc))

    if args.output_format == "json":
        print(json.dumps(report, indent=2, default=str))
    else:
        print_gain_report(
            report,
            show_history=args.history,
            show_daily=args.daily,
            show_graph=args.graph,
        )
    return 0
