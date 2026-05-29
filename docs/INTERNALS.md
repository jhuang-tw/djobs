# Internals

djobs 的核心是一個完整的 durable job queue。本文件描述進階架構、設定、後端選項與模組細節。

如果你只是想讓 AI coding agent 能在 IDE crash 後接續工作，只需要看 [README](../README.md) 的 Quick Start。

---

## Architecture

```
┌─────────────┐     MCP tools      ┌──────────────┐
│  AI Agent   │ ──────────────────> │  MCP Server  │
│  (Copilot)  │ <────────────────── │  (FastMCP)   │
└─────────────┘                     └──────┬───────┘
                                           │
                              ┌────────────┼────────────┐
                              │            │            │
                        ┌─────▼─────┐ ┌────▼────┐ ┌────▼─────┐
                        │  Queue    │ │ Daemon  │ │ Audit    │
                        │  Service  │ │ (Pool + │ │ Log      │
                        │           │ │ Sched)  │ │          │
                        └─────┬─────┘ └─────────┘ └──────────┘
                              │
                        ┌─────▼─────┐
                        │  SQLite   │
                        │  (or PG)  │
                        └───────────┘
```

### Job State Machine

```
pending ──────► running ──────► succeeded
   │               │
   │               ├──────► failed
   │               │
   │               ├──────► retry_scheduled ──► pending (retry)
   │               │
   │               └──────► dead_lettered
   │
   ├──────► succeeded  (AI agent direct complete)
   └──────► failed     (AI agent direct fail)
```

### Module Map

| Module | Responsibility |
|--------|---------------|
| `djobs.core` | Job model, state machine, domain errors |
| `djobs.queue` | Submit, claim, complete, fail, retry logic |
| `djobs.storage` | SQLite & PostgreSQL repositories, event log |
| `djobs.worker` | Handler registry, WorkerPool, WorkerRunner |
| `djobs.scheduler` | Retry promotion, expired lease recovery |
| `djobs.daemon` | Composes WorkerPool + Scheduler into one process |
| `djobs.observability` | Metrics, structured logging, job inspection |
| `djobs.mcp_server` | MCP tool definitions, embedded daemon |
| `djobs.cli` | `djobs serve` CLI entry point |

---

## Using djobs as a Python Library

In addition to the MCP server, djobs can be used directly as a Python job queue:

```python
from djobs import SQLiteJobRepository, QueueService, HandlerRegistry, WorkerPool

# 1. Set up
repo = SQLiteJobRepository.from_path("jobs.db")
queue = QueueService(repo)

# 2. Submit a job
job = queue.submit("send_email", {"to": "user@example.com"}, max_attempts=3)

# 3. Process jobs
registry = HandlerRegistry()
registry.register("send_email", lambda payload: send_email(**payload))

pool = WorkerPool(queue, registry, worker_id="worker-1", max_concurrent=4)
pool.run_loop(stop_event)
```

---

## Full MCP Tool Reference

14 tools are exposed via FastMCP / stdio.

**Core task lifecycle**

| Tool | Purpose |
|------|---------|
| `enqueue_task` | Submit a durable task (survives crashes). Supports `depends_on` and `resource_key`. |
| `complete_task` | Mark task succeeded after agent finishes work |
| `fail_task` | Mark task failed with error message |
| `check_task` | Inspect task status, attempts, duration |
| `list_tasks` | List tasks by correlation_id |
| `resume_session` | Find incomplete tasks from previous sessions |
| `audit_log` | Query event history — "what did the AI do?" |
| `health` | Queue depth by status |

**Multi-agent coordination** (shared queue across several agents)

| Tool | Purpose |
|------|---------|
| `claim_task` | Atomically lease the next ready task; sets a heartbeat lease so other agents skip it |
| `heartbeat_task` | Extend the lease on a claimed task while still working |
| `release_task` | Hand a task back to the queue (e.g. on failure or shutdown) |
| `register_agent` | Register/refresh an agent (capabilities + metadata), marks it ONLINE |
| `agent_heartbeat` | Keep an agent ONLINE; agents idle past the timeout are auto-marked OFFLINE |
| `list_agents` | List agents (optionally filter by status); reaps stale agents first |

Dependencies (`depends_on`) and resource locks (`resource_key`) are passed to
`enqueue_task`: a task with unmet dependencies or a held `resource_key` is simply
not returned by `claim_task` until it becomes ready.

The claim path computes the running-per-type counts and the set of held
`resource_key`s once per claim (not once per candidate), and scans candidate
rows lazily — stopping at the first claimable task — so a backlog of blocked
tasks does not turn each claim into an O(n) scan with per-row subqueries.

---

## Full Capability Summary

| Area | What you get |
|------|--------------|
| **MCP server** | 14 tools exposed via FastMCP / stdio — works in VS Code, Claude Desktop, etc. |
| **Crash recovery** | `resume_session` returns incomplete tasks for a given workspace / correlation id |
| **Audit trail** | `audit_log` aggregates `job_events` so you can answer "what did the AI do yesterday?" |
| **Type isolation** | Built-in daemon only claims job types it has handlers for; AI-only types are left to the agent via `complete_task` / `fail_task` |
| **Multi-agent coordination** | Shared queue with atomic `claim_task` leases, agent registry (`register_agent` / `agent_heartbeat` / `list_agents`), task dependencies (`depends_on`) and resource locks (`resource_key`) |
| **Web dashboard** | Read-only cross-agent fleet + queue view via `djobs dashboard` (stdlib HTTP server, no extra deps) |
| **SQLite first** | No Redis, RabbitMQ, Docker, or Postgres required for local use |
| **Postgres path** | Same `JobRepository` protocol implemented on top of `SELECT ... FOR UPDATE SKIP LOCKED` for multi-worker setups |
| **Test coverage** | 271 passing tests (16 skipped without Postgres), strict ruff lint |

---

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DJOBS_DB_PATH` | `djobs.db` | SQLite database file path |
| `DJOBS_LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `DJOBS_LOG_FORMAT` | `json` | Log output format (`json` or `text`) |
| `DJOBS_WORKER_ID` | `worker-1` | Identifier for this worker instance |

These are read by `Config.from_env()` and used by the daemon / worker pool. The MCP server and CLI default to `djobs_mcp.db` via their own `--db` argument.

### correlation_id Convention

`resume_session` and `list_tasks` filter by `correlation_id`. The recommended convention:

- **VS Code agent**: use the workspace folder path (e.g. `c:\src\my\project` or `/home/user/project`)
- **CI / automation**: use the run ID or pipeline name
- **Multi-repo**: use `{workspace_path}:{repo_name}` to avoid collision

The value is opaque — djobs does not interpret it. Pick any stable string that groups related tasks.

### SQLite Concurrency Notes

SQLite uses file-level locking. On Windows, only one process can write at a time (journal mode is WAL by default, which helps with read concurrency). For single-developer laptop use this is fine. If you need multi-process writes, use the PostgreSQL backend (`pip install "djobs[pg]"`).

### PostgreSQL Backend

For multi-worker or team setups:

```bash
pip install "djobs[pg]"
```

The PostgreSQL backend uses `SELECT ... FOR UPDATE SKIP LOCKED` for atomic job claims. See `migrations/005_postgres_schema.sql` and `src/djobs/storage/postgres.py` for details.

### Dead-Lettered Tasks

After a job exhausts all `max_attempts`, it moves to `dead_lettered` status. These tasks stay in the database for audit purposes but are not retried automatically. To inspect and handle them:

```python
from djobs import SQLiteJobRepository, QueueService

repo = SQLiteJobRepository.from_path("djobs_mcp.db")
queue = QueueService(repo)

# Find dead-lettered tasks
dead = queue.list_by_status("dead_lettered")
for job in dead:
    print(f"{job.id} | {job.type} | {job.last_error}")
    # Resubmit as a fresh job if needed:
    # queue.submit(job.type, job.payload, max_attempts=job.max_attempts,
    #              correlation_id=job.correlation_id)
```

See also: [examples/dead_letter_example.py](../examples/dead_letter_example.py)

---

## How Is This Different From X?

djobs is not the first project to expose a task queue to an AI agent over MCP. It targets a specific combination of properties: SQLite-first, MCP-driven, with crash recovery and audit-log style observability built in.

| Project | Storage | Focus | Closest to djobs? |
|---------|---------|-------|-------------------|
| [TadMSTR/task-queue-mcp](https://github.com/TadMSTR/task-queue-mcp) | YAML files | Multi-agent task hand-off for Claude Code | Closest in spirit. Different storage model (YAML files + dispatcher), no `resume_session` / `audit_log` style observability. |
| [midweste/mcp-cli-gateway](https://github.com/midweste/mcp-cli-gateway) | SQLite | Routing prompts to CLI agents (Gemini / Codex / Claude) with pacing | Overlaps on persistence + observability, but the unit of work is "dispatch a prompt to a CLI", not "durable user task with retry / lease". |
| [j0j1j2/claude-tunnel](https://github.com/j0j1j2/claude-tunnel) | In-memory | Pub/sub + 1:1 request/reply + job queue between Claude Code sessions | Different problem: inter-session messaging, not durable work tracking. |
| Celery / RQ / Dramatiq / Hatchet | Redis / Postgres | General-purpose distributed task queues | Strictly more capable as general queues, but not designed to be driven directly by an AI agent over MCP. |
| Temporal / Inngest / DBOS | Server / SaaS | Durable workflow / execution engines | Much more powerful and much heavier; no MCP integration; not aimed at single-developer laptop use. |

---

## All Examples

```bash
# Codebase migration — crash-proof multi-file refactor (Phase 10 killer demo)
python examples/run_migration_demo.py

# Basic job lifecycle
python examples/run_echo_job.py

# Retry with exponential backoff
python examples/run_retry_job.py

# Concurrent worker pool
python examples/run_pool_demo.py

# Scheduler loop (retry promotion + lease recovery)
python examples/run_scheduler_demo.py

# AI task platform (batch submit + cost tracking)
python examples/run_ai_demo.py

# Durable crash recovery demo (abstract version)
python examples/run_durable_demo.py
```

### Web Dashboard

Read-only fleet + queue view for humans (the agent never starts this):

```bash
djobs dashboard --db djobs_mcp.db --host 127.0.0.1 --port 8787 --refresh 5
# then open http://127.0.0.1:8787
```

The page auto-refreshes and shows queue health, registered agents (with
ONLINE / OFFLINE status), and active tasks with their current lease holder.
A JSON snapshot is available at `GET /api/state`.

---

## Possible Future Improvements

These are not on the active roadmap. They exist as design options if real usage justifies them.

- Async worker support
- Priority queues
- Rate limiting per job type
- HTTP SSE transport for remote MCP
- Kubernetes Job backend
