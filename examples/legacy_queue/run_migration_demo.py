"""Legacy durable-queue demo: crash-proof multi-file task tracking.

This demonstrates the original queue/MCP task subsystem. For current local repository memory,
start with ``examples/memory_walkthrough.py``.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from djobs.mcp_server import (
    check_task,
    complete_task,
    configure,
    enqueue_task,
    health,
    resume_session,
)

DEMO_WORKSPACE = Path(__file__).parent.parent / "demo_workspace"
CORRELATION_ID = str(DEMO_WORKSPACE.resolve())
CRASH_AFTER = 12


def discover_python_files() -> list[Path]:
    """Find all Python files in the bundled legacy demo workspace."""

    return sorted(path for path in DEMO_WORKSPACE.rglob("*.py") if path.name != "__init__.py")


def simulate_docstring_edit(filepath: Path) -> str:
    """Read one file and return the bounded evidence the demo records."""

    first_line = filepath.read_text(encoding="utf-8").split("\n", 1)[0]
    time.sleep(0.05)
    return f"Added docstrings — first line was: {first_line[:60]}"


def main() -> None:
    db_path = str(Path(__file__).with_name("phase10_migration_demo.db"))
    database = Path(db_path)
    if database.exists():
        database.unlink()

    configure(db_path)
    files = discover_python_files()

    print("=" * 64)
    print("  djobs — Legacy Crash-Proof Migration Demo")
    print("=" * 64)
    print()
    print(f"  Workspace : {DEMO_WORKSPACE}")
    print(f"  Files     : {len(files)} Python files to add docstrings")
    print(f"  Crash at  : file #{CRASH_AFTER} (simulated IDE crash)")
    print()

    print("─" * 64)
    print("STEP 1 │ Check for prior work (first session)")
    print("─" * 64)
    result = json.loads(resume_session(CORRELATION_ID))
    print(f"  → {result['message']}")
    print()

    print("─" * 64)
    print("STEP 2 │ Enqueue one task per file")
    print("─" * 64)

    task_ids: list[str] = []
    for filepath in files:
        relative = filepath.relative_to(DEMO_WORKSPACE)
        result_json = enqueue_task(
            task_type="add-docstrings",
            payload=json.dumps({"file": str(relative)}),
            max_attempts=3,
            correlation_id=CORRELATION_ID,
            idempotency_key=f"docstrings:{relative}",
        )
        task = json.loads(result_json)
        task_ids.append(task["id"])
        print(f"  📋 Enqueued: {relative}")

    queue_health = json.loads(health())
    print(
        f"\n  Queue: {queue_health.get('pending', 0)} pending, "
        f"{queue_health.get('total', len(task_ids))} total"
    )
    print()

    print("─" * 64)
    print(f"STEP 3 │ Process files (crash after #{CRASH_AFTER})")
    print("─" * 64)

    for index, (task_id, filepath) in enumerate(zip(task_ids, files, strict=False), 1):
        if index > CRASH_AFTER:
            break
        relative = filepath.relative_to(DEMO_WORKSPACE)
        simulate_docstring_edit(filepath)
        complete_task(task_id)
        print(f"  ✅ [{index:2d}/{len(files)}] {relative}")

    remaining = len(files) - CRASH_AFTER
    print()
    print(f"  💥 IDE CRASHED — {remaining} files still incomplete!")
    print()

    print("─" * 64)
    print("STEP 4 │ New session — resume_session finds unfinished work")
    print("─" * 64)
    configure(db_path)
    result = json.loads(resume_session(CORRELATION_ID))
    print(f"  → {result['message']}")
    print()

    incomplete_tasks = result["tasks"]
    for task in incomplete_tasks:
        payload = task.get("payload", {})
        filename = payload.get("file", "?")
        print(f"  ⏳ {filename:30s}  status={task['status']}")
    print()

    print("─" * 64)
    print("STEP 5 │ Resume — finish remaining files")
    print("─" * 64)

    for index, task in enumerate(incomplete_tasks, 1):
        payload = task.get("payload", {})
        filename = payload.get("file", "?")
        filepath = DEMO_WORKSPACE / filename
        if filepath.exists():
            simulate_docstring_edit(filepath)
        complete_task(task["id"])
        sequence = CRASH_AFTER + index
        print(f"  ✅ [{sequence:2d}/{len(files)}] {filename}")
    print()

    succeeded = 0
    all_ok = True
    for task_id in task_ids:
        info = json.loads(check_task(task_id))
        if info.get("status") == "succeeded":
            succeeded += 1
        else:
            all_ok = False

    print("─" * 64)
    print("RESULT │ Final status")
    print("─" * 64)
    print(f"  Tasks     : {succeeded}/{len(files)} succeeded")
    print(f"  Health    : {health()}")
    print()
    print("  ✅ ALL FILES COMPLETED" if all_ok else "  ❌ Some files still incomplete")
    raise SystemExit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
