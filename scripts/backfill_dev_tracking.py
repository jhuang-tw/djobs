"""One-off: clean demo leftovers and backfill real dev tracking into djobs.

Run from repo root:
    $env:PYTHONPATH="$PWD/src"; .venv/Scripts/python.exe scripts/backfill_dev_tracking.py

What it does:
1. Archives leftover demo tasks (correlation_id == 'docstring-batch-demo').
2. Backfills completed milestones (M1-M5 + docs update) as succeeded tasks.
3. Enqueues remaining roadmap items as pending tasks.

Idempotent: uses idempotency_key so re-running does not duplicate.
"""

from __future__ import annotations

from djobs.queue.service import QueueService
from djobs.storage.sqlite import SQLiteJobRepository

DB_PATH = "djobs_mcp.db"
DEV_CID = "djobs-dev"  # correlation_id for real development work
DEMO_CID = "docstring-batch-demo"

# (type, idempotency_key, payload-summary, evidence)
COMPLETED = [
    ("milestone", "m1-shared-queue",
     "M1: shared queue (claim/heartbeat/release)",
     "Implemented claim_task/heartbeat_task/release_task with atomic leases "
     "(SQLite BEGIN IMMEDIATE, PG FOR UPDATE SKIP LOCKED)."),
    ("milestone", "m2-task-dependency",
     "M2: depends_on dependency DAG",
     "enqueue_task accepts depends_on; tasks with unmet deps are not claimable."),
    ("milestone", "m3-resource-lock",
     "M3: resource_key exclusive locks",
     "enqueue_task accepts resource_key; same key runs at most one task at a time."),
    ("milestone", "m4-agent-registry",
     "M4: agent registry",
     "register_agent/agent_heartbeat/list_agents + auto-offline reaping; "
     "Agent model + AgentStatus enum; migrations/008_agents.sql."),
    ("milestone", "m5-web-dashboard",
     "M5: read-only web dashboard",
     "src/djobs/dashboard.py + `djobs dashboard` CLI; stdlib HTTP server, no new deps."),
    ("docs", "docs-m1-m5-sync",
     "Update docs for M1-M5 drift",
     "Updated INTERNALS/HANDOFF/ROADMAP/ARCHITECTURE: 14 MCP tools, 271 tests, "
     "depends_on/resource_key, agent registry + dashboard sections."),
]

# (type, idempotency_key, payload-summary)
REMAINING = [
    ("roadmap", "rm-http-sse-transport",
     "HTTP SSE transport for remote MCP server"),
    ("roadmap", "rm-agent-role-routing",
     "Agent role-based routing / auto-dispatch policy"),
    ("roadmap", "rm-k8s-backend",
     "Kubernetes Job backend"),
]


def main() -> None:
    repo = SQLiteJobRepository.from_path(DB_PATH)
    queue = QueueService(repo)

    # 1. Archive demo leftovers.
    archived = 0
    for job in repo.list_by_status("pending"):
        if job.correlation_id == DEMO_CID:
            queue.archive(job.id, reason="demo leftover cleanup")
            archived += 1
    print(f"[1] archived {archived} demo task(s)")

    # 2. Backfill completed work as succeeded.
    done = 0
    for job_type, key, summary, evidence in COMPLETED:
        job = queue.submit(
            job_type,
            {"summary": summary},
            correlation_id=DEV_CID,
            idempotency_key=key,
        )
        if job.status.value == "pending":
            queue.complete(job.id, evidence=f"[backfilled] {evidence}")
            done += 1
    print(f"[2] backfilled {done} completed task(s) as succeeded")

    # 3. Enqueue remaining roadmap as pending.
    queued = 0
    for job_type, key, summary in REMAINING:
        job = queue.submit(
            job_type,
            {"summary": summary},
            correlation_id=DEV_CID,
            idempotency_key=key,
        )
        if job.status.value == "pending":
            queued += 1
    print(f"[3] enqueued {queued} remaining roadmap task(s) as pending")

    print("health:", queue.health())


if __name__ == "__main__":
    main()
