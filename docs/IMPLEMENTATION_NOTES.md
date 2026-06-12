# Implementation Notes

This document breaks down Phase 0 state and Phase 1 into next-round executable small steps.

## Phase 0 Implementation Plan

Status: Completed.

Currently completed:

- `.venv`, using Python 3.13.
- `pyproject.toml`, using `src` layout, hatchling, pytest, ruff.
- package init files.
- `Config` dataclass + environment variables.
- structured logging helper.
- `Job` model, `JobStatus`, state transition validator.
- import / config / logging / job model / state transition tests.

Verification command:

```powershell
python -m ruff check .
python -m pytest -v
```

Latest verification: ruff 0 errors, pytest 32 passed.

Following is original Phase 0 plan, kept as historical record and delivery reference.

### Step 1: Python Project Setup

Add:

```text
pyproject.toml
```

Suggested settings:

- project name: `distributed-job-system`.
- package source: `src` layout.
- Python version: 3.13.
- test dependency: `pytest`.
- dev dependency: `ruff`.

If unsure of user's Python version, check local environment first then decide.

### Step 2: Package Init Files

Add:

```text
src/djobs/__init__.py
src/djobs/api/__init__.py
src/djobs/core/__init__.py
src/djobs/observability/__init__.py
src/djobs/queue/__init__.py
src/djobs/scheduler/__init__.py
src/djobs/storage/__init__.py
src/djobs/worker/__init__.py
```

### Step 3: Core Types

Add:

```text
src/djobs/core/states.py
src/djobs/core/models.py
src/djobs/core/errors.py
```

Suggested content:

- `JobStatus` enum / `StrEnum`.
- `Job` dataclass.
- `InvalidStateTransitionError` exception.
- `validate_transition(from_status, to_status)`.

Phase 1 only allows:

```text
pending -> running
running -> succeeded
running -> failed
```

### Step 4: Smoke Test

Add:

```text
tests/unit/test_imports.py
tests/unit/test_job_state.py
```

Tests:

- package import works.
- valid transitions pass.
- invalid transitions raise error.

### Step 5: Run Verification

Suggested command:

```powershell
python -m pytest
python -m ruff check .
```

If environment hasn't installed dev dependency yet, verify with available command, and note in reply which parts are uninstalled.

## Phase 1 Implementation Plan

Status: Completed.

Currently completed:

- `migrations/001_initial.sql`.
- `src/djobs/storage/sqlite.py`: SQLite schema initialization, job repository, minimal event log.
- `src/djobs/queue/service.py`: submit / claim / complete / fail.
- `src/djobs/worker/registry.py`: handler registry.
- `src/djobs/worker/runner.py`: `run_once()` worker runner.
- `examples/run_echo_job.py`: echo job demo.
- unit / integration tests: repository, queue service, worker registry, worker runner, SQLite end-to-end flow.

Verification command:

```powershell
python -m ruff check .
python -m pytest -v
$env:DJOBS_EXAMPLE_DB_PATH="$env:TEMP\djobs_phase1_demo.db"; .\.venv\Scripts\python.exe examples\run_echo_job.py
```

Latest verification: ruff 0 errors, pytest 53 passed, echo demo runs to `succeeded` and produces `job_created`, `job_claimed`, `job_succeeded` events.

Following is original Phase 1 plan, kept as historical record and delivery reference.

Phase 1's goal is single-node durable job queue MVP.

Scope principle: Only implement single-node, synchronous, SQLite-backed flow. Do not implement retry, lease, heartbeat, scheduler, rate limiting, or distributed coordination. But repository interface should leave space for future atomic claim evolution.

### Step 1: SQLite Schema

Add:

```text
src/djobs/storage/sqlite.py
migrations/001_initial.sql
```

First version schema:

```sql
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 1,
    run_after TEXT NULL,
    idempotency_key TEXT NULL,
    last_error TEXT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE job_events (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NULL,
    metadata_json TEXT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
```

Phase 1 records minimal event log:

```text
job_created
job_claimed
job_succeeded
job_failed
```

This is not event sourcing, just audit trail, letting Phase 6's timeline / inspect naturally build on same data.

### Step 2: Repository

Suggested repository methods:

```text
create_job(job)
get_job(job_id)
claim_next_job(worker_id)
mark_succeeded(job_id)
mark_failed(job_id, error)
append_event(job_id, event_type, message, metadata)
```

Phase 1 can first skip multi-process concurrent worker handling, but `claim_next_job` should not scatter as "select externally, update externally". Should be wrapped as single repository method, internally use transaction to make atomic claim shape. Phase 7 replaces SQLite implementation with PostgreSQL row lock + `SKIP LOCKED`.

Suggested `claim_next_job(worker_id)` behavior:

- Find earliest created, `pending` status, and `run_after IS NULL OR run_after <= now` job.
- In same repository operation, update to `running`.
- Write `job_claimed` event.
- Return claimed job; if no job, return `None`.

### Step 3: Queue Service

Add:

```text
src/djobs/queue/service.py
```

Queue service wraps repository, responsible for:

- submit job.
- claim job.
- complete job.
- fail job.

Queue service should handle lifecycle semantics, not expose SQL details directly. Phase 1 only handles `pending -> running -> succeeded/failed`.

### Step 4: Handler Registry

Add:

```text
src/djobs/worker/registry.py
```

Suggested API:

```python
registry.register("demo.echo", handler)
registry.get("demo.echo")
```

Handler signature initially simplified:

```python
def handler(payload: dict) -> dict:
    ...
```

Phase 3 consider context, heartbeat, cancellation.

### Step 5: Worker Runner

Add:

```text
src/djobs/worker/runner.py
```

Worker runner does:

1. claim next job.
2. find handler by job type.
3. execute handler.
4. mark succeeded or failed.
5. repository / queue service write corresponding event log.

Phase 1 can first implement `run_once()`, no need to rush into long-running daemon.

### Step 6: Example

Add:

```text
examples/run_echo_job.py
```

Demo flow:

1. initialize SQLite db.
2. register `demo.echo` handler.
3. submit a job.
4. run worker once.
5. print final job state.

### Step 7: Tests

Suggested tests:

```text
tests/unit/test_queue_service.py
tests/unit/test_worker_registry.py
tests/unit/test_worker_runner.py
tests/integration/test_sqlite_job_flow.py
```

Test focus:

- submit job creates pending job.
- worker success marks succeeded.
- worker exception marks failed.
- unknown handler marks failed with useful reason.
- state changes create event log records.
- `claim_next_job` returns `None` when no job available.
- future `run_after` job is not claimed early.

## Coding Principles

Please next round follow:

- Small submission concepts, do not stuff complete platform at once.
- First domain model, then storage, then worker.
- Every state mutation should be testable.
- Phase 1 needs no async.
- Phase 1 needs no retry.
- Phase 1 needs no scheduler loop.
- Phase 1 needs no Redis / Kafka.
- Phase 1 needs no HTTP server.
- Do not mix business handler and queue internals.
- Every job state mutation should have state transition validation and event log.

## Phase 2 Implementation Notes

Status: Completed.

Currently completed:

- `src/djobs/core/retry.py`: `RetryPolicy` and exponential backoff.
- `src/djobs/core/states.py`: new `retry_scheduled`, `dead_lettered`.
- `src/djobs/core/errors.py`: new `RetryableJobError`, `NonRetryableJobError`.
- `src/djobs/storage/sqlite.py`: retry scheduling, retry promotion, DLQ, active idempotency key lookup.
- `migrations/002_active_idempotency_key.sql`.
- `src/djobs/queue/service.py`: `retry_or_dead_letter()`, `promote_due_retries()`.
- `src/djobs/worker/runner.py`: retryable error goes to retry / DLQ, non-retryable error goes to failed.
- `examples/run_retry_job.py`: retry -> promote -> rerun -> succeeded demo.
- unit / integration tests: retry policy, state machine, idempotency, retry scheduling, DLQ, retry promotion, worker retry flow.

Verification command:

```powershell
python -m ruff check .
python -m pytest -v
$env:DJOBS_RETRY_EXAMPLE_DB_PATH="$env:TEMP\djobs_phase2_retry_demo.db"; .\.venv\Scripts\python.exe examples\run_retry_job.py
```

Latest verification: ruff 0 errors, pytest 78 passed, retry demo runs to `succeeded`, event sequence is `job_created`, `job_claimed`, `retry_scheduled`, `retry_promoted`, `job_claimed`, `job_succeeded`.

## Naming Suggestions

Suggested clear names:

- `JobStatus.PENDING`
- `JobStatus.RUNNING`
- `JobStatus.SUCCEEDED`
- `JobStatus.FAILED`
- `JobRepository`
- `QueueService`
- `WorkerRunner`
- `HandlerRegistry`

## Risks To Watch

### Scope Creep

This project easily wants to do too much at once. Each round pick one testable slice.

### Fake Distributed Claims

If not yet implementing atomic claim, do not claim distributed worker support in README.

### Missing Event Log

Without event log, later observability story weakens. Phase 1 can first simply record created / claimed / succeeded / failed.

### Over-Abstraction

Do not too early implement plugin framework, DAG engine, multi-backend storage abstraction. First stabilize single path.

## Suggested Next Response To User Before Phase 3

Before starting Phase 3, can reply:

```text
Phase 0 to Phase 2 completed, next recommendation is Phase 3: add lease, visibility timeout, worker heartbeat, expired lease detection, and stale running job recovery. First lock scope to crash recovery, don't touch scheduler daemon, rate limiter, or PostgreSQL distributed mode.
```