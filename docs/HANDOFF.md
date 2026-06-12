# AI Handoff Notes

This document is written for the next AI or next round of development to take over. Please read completely before starting.

## User Intent

User wants to use this side project as the main portfolio piece for backend / infra / distributed systems. The topic is Distributed Job System, positioning similar to mini Temporal, mini Airflow, mini Durable Functions, but does not need to be a complete product from the start.

User especially cares about:

- Can complete in phases, do not do everything at once.
- Each phase can accumulate architecture stories for interviews.
- Documentation should be detailed so the next AI can take over.
- Documentation is primarily in Chinese.

## Current Repository State

Current project root directory is:

```text
c:\src\my\distributed-job-system
```

Current folder structure exists:

```text
docker/
examples/
migrations/
scripts/
src/djobs/api/
src/djobs/core/
src/djobs/observability/
src/djobs/queue/
src/djobs/scheduler/
src/djobs/storage/
src/djobs/worker/
tests/integration/
tests/unit/
```

Phase 0–Phase 7 completed. Currently has:

- `.venv`, using Python 3.13.
- `pyproject.toml`, using `src` layout, hatchling, pytest, ruff.
- `src/djobs` package and sub-module `__init__.py`.
- `src/djobs/core/config.py`: dataclass + environment variables.
- `src/djobs/observability/logging.py`: JSON / text structured logging helper.
- `src/djobs/core/models.py`: `Job` dataclass.
- `src/djobs/core/states.py`: `JobStatus` and Phase 1 state transition validator.
- `src/djobs/core/errors.py`: domain errors.
- `src/djobs/storage/sqlite.py`: SQLite schema initialization, job repository, minimal event log.
- `migrations/001_initial.sql`: Phase 1 SQLite schema.
- `src/djobs/queue/service.py`: submit / claim / complete / fail.
- `src/djobs/worker/registry.py`: handler registry.
- `src/djobs/worker/runner.py`: `run_once()` worker runner.
- `examples/run_echo_job.py`: echo job demo.
- `src/djobs/core/retry.py`: `RetryPolicy` and exponential backoff.
- `src/djobs/core/states.py`: `retry_scheduled`, `dead_lettered`.
- `src/djobs/core/errors.py`: `RetryableJobError`, `NonRetryableJobError`.
- `src/djobs/storage/sqlite.py`: retry scheduling, retry promotion, DLQ, active idempotency key lookup.
- `migrations/002_active_idempotency_key.sql`: active idempotency key unique index.
- `examples/run_retry_job.py`: retry -> promote -> rerun -> succeeded demo.
- `src/djobs/scheduler/scheduler.py`: `SchedulerLoop` (`tick()` single run + `run_loop()` continuous), `TickResult`. Each `tick()` does three things: promote due retries, recover expired leases, reap stale agents.
- `examples/run_scheduler_demo.py`: crash recovery + retry promotion complete demo.
- `src/djobs/worker/pool.py`: `WorkerPool` (`max_concurrent` + `ThreadPoolExecutor` + graceful drain).
- `src/djobs/storage/sqlite.py`: `count_by_status`, `count_running_by_type`, per-type `claim_next_job` concurrency limit, `busy_timeout`.
- `src/djobs/queue/service.py`: `backlog()`, `count_running_by_type()`, `claim` supports `type_concurrency_limits`.
- `examples/run_pool_demo.py`: WorkerPool concurrency control + graceful drain demo.
- `src/djobs/core/models.py`: `correlation_id`, `started_at` fields.
- `src/djobs/observability/metrics.py`: `MetricsCollector` (counters + gauges + snapshot).
- `src/djobs/observability/inspect.py`: `inspect_job` job inspection summary.
- `src/djobs/queue/service.py`: `inspect()`, `health()`, `submit` supports `correlation_id`.
- `src/djobs/worker/pool.py`: each job execution duration logging.
- `migrations/004_observability_columns.sql`: `correlation_id`, `started_at` fields.
- unit / integration tests: including metrics, observability inspect, correlation id, health, started_at, full lifecycle integration tests.
- `src/djobs/storage/events.py`: `JobEvent` shared dataclass (extracted from sqlite.py).
- `src/djobs/storage/postgres.py`: `PostgresJobRepository` (`SELECT ... FOR UPDATE SKIP LOCKED` atomic claim).
- `docker/docker-compose.yml`: PostgreSQL 16 service.
- `migrations/005_postgres_schema.sql`: PostgreSQL specific schema (TIMESTAMPTZ).
- `pyproject.toml`: `pg` optional dependency (`psycopg[binary]>=3.1`).
- `tests/integration/test_repository_contract.py`: 16 contract tests, SQLite and PostgreSQL shared, auto-skip when no PostgreSQL.

Latest verification: `python -m ruff check .` passed, `python -m pytest` 162 passed + 16 skipped (PG tests skip when no PostgreSQL).

Phase 8 completed:

- `src/djobs/api/ai_handlers.py`: AI handler simulation (summarize, classify, generate).
- `src/djobs/queue/service.py`: `submit_batch()` batch submission.
- `examples/run_ai_demo.py`: AI platform demo (batch submit + cost tracking).
- `tests/unit/test_ai_handlers.py`, `tests/integration/test_ai_platform.py`.

Verification: `python -m pytest` 172 passed + 16 skipped.

Phase 9 completed:

- `src/djobs/mcp_server.py`: MCP server (stdio transport), exposes 5 tools (enqueue_task, check_task, list_tasks, resume_session, health).
- `.vscode/mcp.json`: VS Code MCP integration settings.
- `.agent.md`: Durable Coder agent rule definition.
- `examples/run_durable_demo.py`: crash recovery demo (enqueue → partial processing → simulate crash → resume → complete).
- `pyproject.toml`: `mcp` optional dependency (`mcp[cli]>=1.0`).
- `tests/unit/test_mcp_server.py`: MCP tool unit tests.

Verification: `python -m ruff check .` passed, `python -m pytest` 188 passed + 16 skipped.

## Multi-Agent Evolution (M1–M5, Completed)

After Phase 9, multi-agent orchestration completed (corresponding to ROADMAP Phase 14a), allowing multiple AI agents to safely share the same queue:

- **M1 shared queue**: `claim_task` (atomic lease), `heartbeat_task` (extend lease), `release_task` (return).
- **M2 task dependency**: `enqueue_task` `depends_on`, task only claimable after all dependencies succeeded.
- **M3 resource lock**: `enqueue_task` `resource_key`, only one task with same key can run at a time.
- **M4 agent registry**: `register_agent` / `agent_heartbeat` / `list_agents`, auto-mark OFFLINE if heartbeat timeout.
- **M5 web dashboard**: `djobs dashboard` (stdlib HTTP server, read-only), show queue health, agent fleet, active tasks, default http://127.0.0.1:8787.

MCP server currently exposes **14 tools** (8 core + 6 multi-agent). `src/djobs/dashboard.py` is new module; `migrations/006`–`008` cover lease / dependency / resource_key / agents fields.

Latest verification: `python -m ruff check .` passed, `python -m pytest` 271 passed + 16 skipped.

## Storage / Schema Convergence (0.6.0, Completed)

- **single schema authority**: new `src/djobs/storage/schema.py`, becomes runtime schema source of truth for both backends. `SQLITE_SCHEMA_SQL` and `POSTGRES_SCHEMA_SQL` placed side-by-side, field upgrades driven by single `JOBS_COLUMN_MIGRATIONS` list (`apply_sqlite_column_migrations` / `apply_postgres_column_migrations`). `sqlite.py`, `postgres.py` no longer embed DDL separately, instead import and keep old names `SCHEMA_SQL` / `PG_SCHEMA_SQL` (backward compatible).
- **`migrations/*.sql` positioning clarified**: they are **historical / manual** migration records, supplied for operator to manually apply to existing DB, **not** runtime migration runner; new databases always created by `schema.py`.
- **drift guard tests**: `tests/unit/test_schema.py` ensures SQLite / PostgreSQL schema logical fields consistent, old DB upgradeable and idempotent. `tests/unit/test_concurrency.py` adds atomic claim concurrency tests (no duplicate claim, no loss).
- **storage strategy**: maintain raw SQL (queue correctness needs precise lock / claim semantics control), no ORM / SQLAlchemy. SQLite as default, PostgreSQL as optional (`pip install "djobs[pg]"`).

## Next AI Should Do First

Phase 0–9 and multi-agent M1–M5 all completed. Remaining optional directions:

- HTTP SSE transport for remote MCP server usage.
- Agent role-based routing / automatic dispatch policy.
- Kubernetes Job backend.

## Do Not Do Yet

Please do not do these yet, avoid scope creep:

- Do not start with Web UI.
- Do not start connecting Redis, Kafka, Temporal, Celery.
- Do not start with Kubernetes.
- Do not start with complete DAG engine.
- Do not start pursuing exactly-once.

## Recommended Technology Choices

Initial recommendations:

- Language: Python.
- Storage: SQLite for Phase 1 to Phase 3.
- Tests: pytest.
- Lint / format: ruff.
- CLI: argparse or typer. For fewer dependencies, start with argparse.
- DB access: Start with standard library `sqlite3`, evaluate SQLAlchemy when schema gets complex.
- Config: dataclass + environment variables.

Rationale: This project's value is in distributed systems design, not framework novelty. Fewer dependencies make core concepts easier to demonstrate.

## First Implementation Slice

If the next AI starts writing code, suggested first batch of files:

```text
src/djobs/core/models.py
src/djobs/storage/sqlite.py
src/djobs/queue/service.py
src/djobs/worker/runner.py
tests/unit/test_sqlite_lease.py
tests/unit/test_queue_service_lease.py
tests/integration/test_sqlite_crash_recovery_flow.py
```

First batch should not write scheduler daemon, rate limiter, or PostgreSQL. First get claim -> lease -> heartbeat -> expired lease recovery flow testable.

## Suggested Initial Domain Model

Keep job fields simple initially:

- `id`: unique job id.
- `type`: handler type, such as `send_email`, `ai_summarize`.
- `payload`: JSON payload.
- `status`: `pending`, `running`, `succeeded`, `failed`.
- `attempt`: current attempt number.
- `max_attempts`: maximum retry count.
- `created_at`: create time.
- `updated_at`: update time.
- `run_after`: delayed execution time; Phase 1 can reserve field but not implement complete scheduler.
- `idempotency_key`: Phase 2 usage; Phase 1 can reserve concept.

## Suggested State Machine

Phase 1 supports:

```text
pending -> running -> succeeded
pending -> running -> failed
```

Phase 2 adds:

```text
running -> retry_scheduled -> pending
running -> dead_lettered
```

Phase 3 adds lease recovery:

```text
running -> pending
```

This transition should only happen when lease expires or worker lost, not arbitrarily called by general handler.

## Definition Of Done For Phase 0

Status: Completed.

Phase 0 completion criteria:

- Can execute `pytest`.
- package can import normally from `src` layout.
- README and docs don't overstate completed capability.
- Have minimum tests to ensure environment correct.
- No unnecessary heavy dependencies introduced.

## Definition Of Done For Phase 1

Status: Completed.

Phase 1 completion criteria:

- Can submit job.
- Job persists to SQLite.
- Worker can claim pending job.
- Worker can execute registered handler.
- On success, job becomes `succeeded`.
- On failure, job becomes `failed`, error reason saved.
- Every Phase 1 state mutation writes minimal event log.
- `claim_next_job` wrapped as repository method, reserve space for future atomic claim evolution.
- Worker runner has `run_once()`.
- At least unit tests cover state transition and repository.
- At least one example can complete a demo job.

## Definition Of Done For Phase 2

Status: Completed.

Phase 2 completion criteria:

- Retryable handler failure enters `retry_scheduled`.
- Non-retryable handler failure enters `failed`.
- Retry exhausted enters `dead_lettered`.
- After `run_after` expires, can promote back to `pending`.
- Active idempotency key does not create duplicate active job.
- Event log records retry scheduled, retry promoted, dead-lettered.
- Retry demo can complete retry -> promote -> succeeded.

## Communication Style For Future AI

Please when replying to user:

- Use Traditional Chinese.
- Briefly say what is currently completed.
- Clearly state recommendations for next steps.
- Do not dump too large an implementation plan at once.
- If modifying files, list main files and verification method at end.

