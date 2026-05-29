"""Phase 10 demo: Codebase Migration — crash-proof multi-file refactor.

Simulates the real-world scenario that makes djobs valuable:

    "Add docstrings to 20 files in my project.
     VS Code crashed after file 12.
     When I reopen, the agent picks up from file 13 — no work lost."

This demo:
  1. Scans examples/demo_workspace/ for Python files.
  2. Enqueues each file as an add-docstrings task via MCP tools.
  3. Processes 12 files, then simulates an IDE crash.
  4. Restarts, calls resume_session to discover the remaining 8 files.
  5. Finishes all 20 — zero data loss.

Run:
    python examples/run_migration_demo.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Import djobs MCP tool functions directly (same functions the AI agent calls)
# ---------------------------------------------------------------------------
from djobs.mcp_server import (
    check_task,
    complete_task,
    configure,
    enqueue_task,
    health,
    resume_session,
)

DEMO_WORKSPACE = Path(__file__).parent / "demo_workspace"
CORRELATION_ID = str(DEMO_WORKSPACE.resolve())

# How many files to complete before simulating the crash
CRASH_AFTER = 12


def discover_python_files() -> list[Path]:
    """Find all .py files in the demo workspace (excluding __init__.py)."""
    files = sorted(
        p for p in DEMO_WORKSPACE.rglob("*.py") if p.name != "__init__.py"
    )
    return files


def simulate_docstring_edit(filepath: Path) -> str:
    """Pretend the AI agent read the file and added docstrings.

    In real usage, the agent would actually edit the file via its normal
    code-editing tools.  Here we just read the first line to prove we
    touched the right file, and return a short summary.
    """
    first_line = filepath.read_text(encoding="utf-8").split("\n", 1)[0]
    time.sleep(0.05)  # simulate LLM latency
    return f"Added docstrings — first line was: {first_line[:60]}"


def main() -> None:
    # Clean slate
    db_path = str(Path(__file__).with_name("phase10_migration_demo.db"))
    p = Path(db_path)
    if p.exists():
        p.unlink()

    configure(db_path)
    files = discover_python_files()

    print("=" * 64)
    print("  djobs — Crash-Proof Codebase Migration Demo")
    print("=" * 64)
    print()
    print(f"  Workspace : {DEMO_WORKSPACE}")
    print(f"  Files     : {len(files)} Python files to add docstrings")
    print(f"  Crash at  : file #{CRASH_AFTER} (simulated IDE crash)")
    print()

    # ==================================================================
    # STEP 1 — Agent starts a new chat: "Add docstrings to all files"
    # ==================================================================
    print("─" * 64)
    print("STEP 1 │ Check for prior work (first session)")
    print("─" * 64)

    result = json.loads(resume_session(CORRELATION_ID))
    print(f"  → {result['message']}")
    print()

    # ==================================================================
    # STEP 2 — Enqueue every file as a durable task
    # ==================================================================
    print("─" * 64)
    print("STEP 2 │ Enqueue one task per file")
    print("─" * 64)

    task_ids: list[str] = []
    for f in files:
        relative = f.relative_to(DEMO_WORKSPACE)
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

    h = json.loads(health())
    print(f"\n  Queue: {h.get('pending', 0)} pending, {h.get('total', len(task_ids))} total")
    print()

    # ==================================================================
    # STEP 3 — Process files one by one… then CRASH
    # ==================================================================
    print("─" * 64)
    print(f"STEP 3 │ Process files (crash after #{CRASH_AFTER})")
    print("─" * 64)

    for i, (tid, f) in enumerate(zip(task_ids, files, strict=False), 1):
        if i > CRASH_AFTER:
            break

        relative = f.relative_to(DEMO_WORKSPACE)
        simulate_docstring_edit(f)
        complete_task(tid)
        print(f"  ✅ [{i:2d}/{len(files)}] {relative}")

    remaining = len(files) - CRASH_AFTER
    print()
    print(f"  💥 IDE CRASHED — {remaining} files still incomplete!")
    print("     (In real life: VS Code closes, chat history gone)")
    print()

    # ==================================================================
    # STEP 4 — User reopens IDE, agent calls resume_session
    # ==================================================================
    print("─" * 64)
    print("STEP 4 │ New session — resume_session finds unfinished work")
    print("─" * 64)

    # Simulate a fresh process: re-configure from the same DB
    configure(db_path)

    result = json.loads(resume_session(CORRELATION_ID))
    print(f"  → {result['message']}")
    print()

    incomplete_tasks = result["tasks"]
    for t in incomplete_tasks:
        payload = t.get("payload", {})
        fname = payload.get("file", "?")
        print(f"  ⏳ {fname:30s}  status={t['status']}")

    print()

    # ==================================================================
    # STEP 5 — Resume processing from where we left off
    # ==================================================================
    print("─" * 64)
    print("STEP 5 │ Resume — finish remaining files")
    print("─" * 64)

    completed_before = CRASH_AFTER
    for i, t in enumerate(incomplete_tasks, 1):
        payload = t.get("payload", {})
        fname = payload.get("file", "?")
        filepath = DEMO_WORKSPACE / fname

        if filepath.exists():
            simulate_docstring_edit(filepath)

        complete_task(t["id"])
        seq = completed_before + i
        print(f"  ✅ [{seq:2d}/{len(files)}] {fname}")

    print()

    # ==================================================================
    # FINAL — Verify everything completed
    # ==================================================================
    print("─" * 64)
    print("RESULT │ Final status")
    print("─" * 64)

    all_ok = True
    succeeded = 0
    for tid in task_ids:
        info = json.loads(check_task(tid))
        status = info.get("status", "?")
        if status == "succeeded":
            succeeded += 1
        else:
            all_ok = False

    h = json.loads(health())
    print(f"  Tasks     : {succeeded}/{len(files)} succeeded")
    print(f"  Health    : {json.dumps(h)}")
    print()

    if all_ok:
        print("  ✅ ALL FILES COMPLETED — zero data loss after crash")
    else:
        print("  ❌ Some files still incomplete")

    print()
    print("  This is what djobs does: your AI agent's multi-file work")
    print("  survives crashes. No progress lost, no re-doing finished files.")
    print()

    return sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
