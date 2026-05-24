# djobs

**A SQLite-backed durable task queue with first-class MCP integration**, purpose-built for AI coding agents that need crash recovery, audit trails, and zero infrastructure.

[![CI](https://github.com/jhuang-tw/djobs/actions/workflows/ci.yml/badge.svg)](https://github.com/jhuang-tw/djobs/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue.svg)](https://www.python.org/downloads/)

---

## Why djobs?

AI coding agents (GitHub Copilot, Cursor, Cline, etc.) often run multi-file tasks that can take several minutes. When the IDE crashes or the chat disconnects mid-way, in-flight progress is usually lost because the agent's state lives only in chat history.

djobs gives agents a small, durable checkpoint queue so they can resume exactly where they stopped:

```
Agent: "Add docstrings to 12 files"
  -> enqueue 12 tasks (crash-safe checkpoint)
  -> edit file -> complete_task
  -> edit file -> complete_task
  -> ... IDE crashes after file 7 ...

New chat: "hi"
  -> resume_session -> 5 incomplete tasks found
  -> auto-resume from file 8 — no questions asked
```

Under the hood it is a fairly conventional durable job queue (state machine, retry policy, lease, scheduler, event log). The interesting part is how it is wired to AI agents: an MCP server with `enqueue_task` / `complete_task` / `resume_session` / `audit_log`, plus an embedded background daemon, plus a `type_filter` so daemon-managed jobs and agent-managed jobs do not fight over the same queue.

### What is in the box

| Area | What you get |
|------|--------------|
| **MCP server** | 8 tools exposed via FastMCP / stdio — works in VS Code, Claude Desktop, etc. |
| **Crash recovery** | `resume_session` returns incomplete tasks for a given workspace / correlation id |
| **Audit trail** | `audit_log` aggregates `job_events` so you can answer "what did the AI do yesterday?" |
| **Type isolation** | Built-in daemon only claims job types it has handlers for; AI-only types are left to the agent via `complete_task` / `fail_task` |
| **SQLite first** | No Redis, RabbitMQ, Docker, or Postgres required for local use |
| **Postgres path** | Same `JobRepository` protocol implemented on top of `SELECT ... FOR UPDATE SKIP LOCKED` for multi-worker setups |
| **Test coverage** | 214 passing tests (16 skipped without Postgres), strict ruff lint |

---

## Quick Start

### As a Python Library

```bash
pip install djobs
```

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

### As an MCP Server (for AI Agents)

Add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "djobs": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "djobs.mcp_server"],
      "autoApprove": [
        "health", "resume_session", "check_task", "complete_task",
        "fail_task", "list_tasks", "enqueue_task", "audit_log"
      ]
    }
  }
}
```

Then any AI agent can call these MCP tools:

| Tool | Purpose |
|------|---------|
| `enqueue_task` | Submit a durable task (survives crashes) |
| `complete_task` | Mark task succeeded after agent finishes work |
| `fail_task` | Mark task failed with error message |
| `resume_session` | Find incomplete tasks from previous sessions |
| `check_task` | Inspect task status, attempts, duration |
| `list_tasks` | List tasks by correlation_id |
| `audit_log` | Query event history — "what did the AI do?" |
| `health` | Queue depth by status |

---

## How is this different from X?

djobs is not the first project to expose a task queue to an AI agent over MCP. It targets a specific combination of properties: SQLite-first, MCP-driven, with crash recovery and audit-log style observability built in.

| Project | Storage | Focus | Closest to djobs? |
|---------|---------|-------|-------------------|
| [TadMSTR/task-queue-mcp](https://github.com/TadMSTR/task-queue-mcp) | YAML files | Multi-agent task hand-off for Claude Code | Closest in spirit. Different storage model (YAML files + dispatcher), no `resume_session` / `audit_log` style observability. |
| [midweste/mcp-cli-gateway](https://github.com/midweste/mcp-cli-gateway) | SQLite | Routing prompts to CLI agents (Gemini / Codex / Claude) with pacing | Overlaps on persistence + observability, but the unit of work is "dispatch a prompt to a CLI", not "durable user task with retry / lease". |
| [j0j1j2/claude-tunnel](https://github.com/j0j1j2/claude-tunnel) | In-memory | Pub/sub + 1:1 request/reply + job queue between Claude Code sessions | Different problem: inter-session messaging, not durable work tracking. |
| Celery / RQ / Dramatiq / Hatchet | Redis / Postgres | General-purpose distributed task queues | Strictly more capable as general queues, but not designed to be driven directly by an AI agent over MCP. |
| Temporal / Inngest / DBOS | Server / SaaS | Durable workflow / execution engines | Much more powerful and much heavier; no MCP integration; not aimed at single-developer laptop use. |

In one sentence: **djobs is what you reach for when you want a small, Celery-shaped Python job queue, driven mostly by an AI agent through MCP, on a single developer machine, with SQLite.**

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

## Examples

```bash
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

# Durable crash recovery demo
python examples/run_durable_demo.py
```

---

## Development

```bash
git clone https://github.com/jhuang-tw/djobs.git
cd djobs
python -m venv .venv && .venv/bin/activate
pip install -e ".[dev,mcp]"

pytest -q              # 214 tests (16 skipped without Postgres)
ruff check src/ tests/ # lint
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

## Roadmap

- [x] Durable job queue with retry, lease, heartbeat
- [x] SQLite + PostgreSQL backends
- [x] Worker pool with concurrency control
- [x] Scheduler (retry promotion + lease recovery)
- [x] Event sourcing & audit trail
- [x] MCP server with 8 tools
- [x] Embedded daemon (auto-start with MCP)
- [x] Type isolation (daemon vs. AI agent tasks)
- [ ] `pip install djobs` on PyPI
- [ ] Async worker support
- [ ] Priority queues
- [ ] Web dashboard for audit trail
- [ ] Rate limiting per job type

---

## License

[MIT](LICENSE)
