# Architecture Notes

This document describes target architecture and module boundaries. Please note that most current content is design direction, not completed implementation.

## System Overview

Distributed Job System core flow:

```text
Client / API
  -> create job
  -> persist job
  -> enqueue pending job
  -> worker claims job
  -> worker executes handler
  -> update state
  -> write event log
  -> expose logs / metrics / timeline
```

Initial version uses single-machine SQLite, later evolves to PostgreSQL and multiple workers.

## Core Concepts

### Job

Job is a durable unit of work.

Job should contain:

- identity: `id`.
- routing: `type`.
- input: `payload`.
- state: `status`.
- retry: `attempt`, `max_attempts`.
- time: `created_at`, `updated_at`, `run_after`.
- safety: `idempotency_key`.
- execution: `leased_by`, `lease_expires_at`, needed starting Phase 3.
- coordination: `depends_on` (list of job ids that must succeed before this can be claimed), `resource_key` (only one task with this key runs at a time), multi-agent (M2 / M3) beginning.

### Handler

Handler is the function or class that actually executes the job.

Handler design principles:

- Should be idempotent as much as possible.
- Do not directly modify job table; report results through worker / queue service.
- Errors should be categorizable as retryable or non-retryable.
- Long tasks should support heartbeat starting Phase 3.

### Worker

Worker is responsible for:

- Claiming job from queue.
- Finding corresponding handler.
- Executing handler.
- Updating job status.
- Writing event log.
- Maintaining heartbeat starting Phase 3.
- Graceful shutdown, strengthened Phase 3 to Phase 5.

### Queue

Queue is not simply in-memory list, but "persistable, recoverable job selection mechanism".

Queue service is responsible for:

- enqueue.
- claim next job.
- complete job.
- fail job.
- schedule retry.
- move to DLQ.
- recover expired lease.

### Storage

Storage is the durable source of truth.

Phase 1 to Phase 3: SQLite.

Phase 7: PostgreSQL.

Storage should avoid letting other modules write SQL directly. Recommend encapsulation through repository or storage adapter.

### Event Log

Event log is the foundation of observability and debugging.

Phase 1 builds minimal event log, only records core lifecycle events. Phase 2 onwards adds retry / DLQ events; Phase 6 organizes event log into timeline, inspect command, and metrics source.

Important events should be recorded:

- job created.
- job claimed.
- job started.
- job succeeded.
- job failed.
- retry scheduled.
- job dead-lettered.
- lease expired.
- job recovered.

Event log is not necessarily event sourcing. Initial version can just be audit trail.

### Agent (multi-agent, implemented)

Agent represents a worker (usually an AI coding agent) sharing the same queue.

Agent registry is responsible for:

- `register_agent`: register / update agent (capabilities + metadata), mark ONLINE.
- `agent_heartbeat`: maintain agent ONLINE.
- `list_agents`: list agents, can filter by status.
- Agents without heartbeat timeout marked OFFLINE: `SchedulerLoop.tick()` actively calls `reap_stale_agents` each period (not just lazy cleanup when `list_agents` / dashboard read).

Combined with Job's `leased_by` / `lease_expires_at`, multiple agents can safely share same DB: `claim` ensures same task not claimed by two agents simultaneously via atomic lease.

### Dashboard (Implemented, Read-Only)

Dashboard is human-readable local-first read-only view, does not participate in scheduling decisions.

- `djobs dashboard` starts stdlib HTTP server (default http://127.0.0.1:8787).
- Shows queue health, agent fleet (ONLINE / OFFLINE), active tasks and their lease holder.
- `GET /api/state` provides JSON snapshot.
- No new dependencies; agent does not auto-start dashboard.
- **Security**: dashboard has no authentication, default only binds to `127.0.0.1`. Do not bind to `0.0.0.0` or external network; for remote access use SSH tunnel instead. Binding to non-loopback address prints warning.

## Module Boundaries

### `src/djobs/core`

Place domain model and rules.

Appropriate to include:

- Job dataclass / model.
- JobStatus enum.
- state transition validator.
- retry policy dataclass.
- domain exceptions.
- `Agent` dataclass and `AgentStatus` enum (multi-agent).

Should not include:

- SQL.
- worker loop.
- CLI parsing.

### `src/djobs/storage`

Place persistence adapter.

Appropriate to include:

- SQLite connection helper.
- schema initialization.
- job repository.
- event repository.

Should not include:

- handler execution.
- retry policy business decisions, except for storing fields.

### `src/djobs/queue`

Place queue operations and job lifecycle coordination.

Appropriate to include:

- enqueue job.
- claim next job.
- mark succeeded.
- mark failed.
- schedule retry.
- DLQ transition.
- lease recovery.

Should not include:

- Concrete handler business logic.
- HTTP routes.

### `src/djobs/worker`

Place worker runtime.

Appropriate to include:

- worker loop.
- handler registry.
- execution result mapping.
- heartbeat loop.
- shutdown handling.

Should not include:

- Raw SQL.
- scheduler policy.

### `src/djobs/scheduler`

Place time-based promotion.

Appropriate to include:

- delayed job promotion.
- recurring job expansion.
- retry due job promotion.

Should not include:

- handler execution.

### `src/djobs/observability`

Place logs, metrics, timeline helpers.

Appropriate to include:

- structured logging config.
- metrics collector interface.
- event timeline formatter.
- correlation id utilities.

Should not include:

- Job state mutation business logic.

### `src/djobs/api`

Place external entry point.

Initial version can be CLI, add HTTP API later.

Appropriate to include:

- submit command.
- worker command.
- inspect job command.
- list queue command.

## State Machine

### Phase 1 Minimal State Machine

```text
pending -> running -> succeeded
pending -> running -> failed
```

Rules:

- Only pending job can be claimed as running.
- Running job can become succeeded or failed.
- Succeeded is terminal state.
- Failed in Phase 1 can be terminal state.

### Phase 2 Retry State Machine

```text
pending -> running -> succeeded
pending -> running -> retry_scheduled -> pending
pending -> running -> dead_lettered
pending -> running -> failed
```

Rules:

- On retryable failure and attempts not reached limit, enter `retry_scheduled`.
- After retry due, return to `pending`.
- After attempts reach limit, enter `dead_lettered`.
- Non-retryable failure can directly `failed` or `dead_lettered`, decide in Phase 2.

### Phase 3 Lease Recovery

```text
running -> pending
```

Rules:

- Only happens when lease expired / worker stale.
- Must write event log, otherwise very hard to debug duplicate execution.
- This means system semantics is at-least-once, not exactly-once.

## Data Model Draft

Phase 1 can start with one `jobs` table and one `job_events` table.

`jobs` draft:

```text
id TEXT PRIMARY KEY
type TEXT NOT NULL
payload_json TEXT NOT NULL
status TEXT NOT NULL
attempt INTEGER NOT NULL DEFAULT 0
max_attempts INTEGER NOT NULL DEFAULT 1
run_after TEXT NULL
idempotency_key TEXT NULL
last_error TEXT NULL
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
```

Phase 3 adds:

```text
leased_by TEXT NULL
lease_expires_at TEXT NULL
heartbeat_at TEXT NULL
```

`job_events` draft:

```text
id TEXT PRIMARY KEY
job_id TEXT NOT NULL
event_type TEXT NOT NULL
message TEXT NULL
metadata_json TEXT NULL
created_at TEXT NOT NULL
```

## Delivery Semantics

Initial version should clearly adopt at-least-once delivery.

Means:

- Job may be executed more than once.
- System tries to avoid duplicate claim.
- Crash recovery may cause same job to re-execute.
- Handler must consider idempotency.

Do not claim exactly-once. True exactly-once requires very strict external side effect coordination, usually impractical.

## Failure Handling Philosophy

Failure should not just be a boolean.

Need to distinguish:

- handler business failure.
- transient infrastructure failure.
- worker crash.
- timeout.
- poison message.
- downstream rate limit.

Phase 1 can first simplify to success / failure. Phase 2 onwards classify retryable / non-retryable.

## Observability Philosophy

Every job should answer:

- When was it created?
- Which worker claimed it?
- How many times has it run?
- What happened each attempt?
- Which state is it stuck in now?
- What was the last error?

These answers should come from event log, job table, structured logs, not memory or console print.

## Future Distributed Design

When PostgreSQL distributed mode, claiming job should be atomic.

Possible strategies:

- `SELECT ... FOR UPDATE SKIP LOCKED`.
- Single `UPDATE ... WHERE id = (...) RETURNING *`.
- Advisory lock for scheduler leader.
- Transaction per claim.

Must avoid:

- First select pending job externally, then update separately, causing race condition.
- Worker local memory as source of truth.
- Long-running job without lease.

