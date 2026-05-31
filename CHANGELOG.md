# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Versioning policy

This repository ships two independently versioned artifacts:

- **`djobs` Python package** — the durable queue, MCP server, and CLI. It is
  **pre-1.0**: the public API may still change between minor versions.
- **`djobs` VS Code extension** (under `vscode-ext/`) — a thin, read-only
  sidebar over the CLI. Its UI surface is small and stable, so it carries its
  own version line and may sit at a higher number than the Python package.

Entries below are tagged `[core]` or `[ext]` when a change applies to only one
artifact.

## [Unreleased]

## [0.6.0] - 2026-05-30

### Changed
- `[core]` **Schema authority consolidated** into a single module
  `src/djobs/storage/schema.py`. It is now the runtime source of truth for both
  backends: `SQLITE_SCHEMA_SQL` and `POSTGRES_SCHEMA_SQL` live side by side, and
  the post-initial column upgrades are driven by one `JOBS_COLUMN_MIGRATIONS`
  list via `apply_sqlite_column_migrations` / `apply_postgres_column_migrations`.
  `sqlite.py` and `postgres.py` no longer embed their own DDL; they import from
  `schema.py` and re-export the historical names `SCHEMA_SQL` / `PG_SCHEMA_SQL`
  for backward compatibility.
- `[core]` Clarified that `migrations/*.sql` are a **historical / manual**
  migration record for operators applying changes to a pre-existing database by
  hand — they are **not** executed by a runtime migration runner. Fresh
  databases are created from `schema.py`.

### Added
- `[core]` **Backend schema drift guard** — `tests/unit/test_schema.py` asserts
  that the SQLite and PostgreSQL schemas declare the same logical columns, that
  every migrated column already exists in the current schema, and that the
  SQLite column-migration path upgrades a pre-existing database idempotently.
- `[core]` **Atomic-claim concurrency tests** — `tests/unit/test_concurrency.py`
  proves no task is ever double-claimed or lost under contention, both with a
  shared repository (single connection + lock) and with separate connections
  (exercising the database-level `BEGIN IMMEDIATE` write lock).

### Notes
- Raw SQL remains the intentional storage strategy for queue correctness; no
  ORM / SQLAlchemy is introduced. SQLite is the first-class default, with the
  PostgreSQL backend optional via `pip install "djobs[pg]"`.

## [0.5.0] - 2026-05-29

### Added
- `[core]` **Multi-agent coordination** — several agents can safely share one
  queue:
  - `claim_task` / `heartbeat_task` / `release_task` MCP tools with atomic
    leases (SQLite `BEGIN IMMEDIATE`, PostgreSQL `FOR UPDATE SKIP LOCKED`).
  - Task dependencies via `enqueue_task(depends_on=...)` — a task is not
    claimable until all of its dependencies have succeeded.
  - Resource locks via `enqueue_task(resource_key=...)` — at most one task per
    key runs at a time.
  - Agent registry: `register_agent`, `agent_heartbeat`, `list_agents`, with
    automatic OFFLINE reaping of agents that stop heartbeating.
- `[core]` **Read-only web dashboard** — `djobs dashboard` serves a
  cross-agent view of queue health, tasks, and the live agent fleet at
  `http://127.0.0.1:8787` (stdlib HTTP server, no extra dependencies).
- `[core]` CI now runs the repository contract tests against a real PostgreSQL
  service container; `DJOBS_REQUIRE_PG=1` turns a missing database into a hard
  failure instead of a silent skip.
- `[ext]` Sidebar falls back to a human-readable payload field
  (`summary` / `title` / `name`) instead of showing a raw task id.
- `[core]` Job payloads are now size-limited (default 256 KiB) to protect the
  database and claim scans from oversized blobs. Configure via the
  `DJOBS_MAX_PAYLOAD_BYTES` environment variable or the `max_payload_bytes`
  argument to `QueueService`; set to `0` to disable. Oversized payloads raise
  `PayloadTooLargeError`.
- Static type checking with `mypy` is now part of CI (new `typecheck` job) and
  the `dev` extra. `src/djobs` is type-clean under `mypy --ignore-missing-imports`.

### Changed
- `[core]` MCP server now exposes **14 tools** (8 core + 6 multi-agent).
- `[core]` `succeeded → archived` is now a valid state transition, so completed
  workflows can be archived for cleanup.
- `[ext]` Sidebar labels use neutral "task(s)" wording instead of "file(s)".
- `[core]` `SchedulerLoop.tick()` now proactively reaps stale agents every
  cycle (previously they were only marked OFFLINE lazily when `list_agents` or
  the dashboard read them). `TickResult` gained a `reaped` counter.
- `[core]` Optimized the job-claim path in both backends: running-per-type
  counts and held `resource_key`s are now computed once per claim instead of
  once per candidate row (eliminating an N+1 query pattern), and candidate rows
  are scanned lazily so the claim stops at the first ready task.

### Security
- `[core]` The dashboard logs a warning when bound to a non-loopback address,
  reminding operators that it has no authentication and should only be exposed
  on a trusted network. CLI `--host` help documents the same caveat.

### Fixed
- `[core]` Documentation drift across `docs/` (tool count, test count,
  `depends_on` / `resource_key`, agent registry, and dashboard sections).

## [0.3.0] and earlier

Foundational releases, developed in phases:

- Durable SQLite job queue with a state machine (pending → running →
  succeeded / failed), retry with exponential backoff, and a dead-letter queue.
- Lease-based claiming and expired-lease recovery; `SchedulerLoop` and
  `WorkerPool` with graceful drain.
- Observability: structured logging, metrics, job inspection, and an event log
  that powers the `audit_log` MCP tool.
- PostgreSQL backend behind the same `JobRepository` protocol, plus a shared
  repository contract test suite.
- MCP server (stdio) with crash-recovery tools (`enqueue_task`, `complete_task`,
  `fail_task`, `check_task`, `list_tasks`, `resume_session`, `audit_log`,
  `health`) and the `djobs` CLI.

[Unreleased]: https://github.com/jhuang-tw/djobs/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/jhuang-tw/djobs/releases/tag/v0.5.0
