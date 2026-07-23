"""Authoritative database schema for the durable job queue.

This module is the **single source of truth** for the runtime schema applied to
fresh databases, for both the SQLite and PostgreSQL backends. Keeping both
dialects side by side here (instead of buried inside each repository module)
makes drift between them obvious in code review.

Relationship to ``migrations/*.sql``
------------------------------------
The numbered files under the top-level ``migrations/`` directory are the
**historical, incremental record** of how the schema evolved. They are NOT run
by the application at runtime — they exist for operators applying changes to a
pre-existing production database by hand. The DDL below is the cumulative,
current-state schema that the code actually executes via ``CREATE TABLE IF NOT
EXISTS``. When you add a column or index, update BOTH:

1. The relevant schema string here (so fresh DBs get it), and
2. The column-migration list here (so already-existing DBs are upgraded), and
3. Add a new ``migrations/NNN_*.sql`` file for the hand-apply audit trail.
"""

from __future__ import annotations

import sqlite3
from typing import Any

# ---------------------------------------------------------------------------
# Shared observation schema. These tables are intentionally separate from jobs:
# recording repository/tool/session evidence must never change task ownership.
# ---------------------------------------------------------------------------

SQLITE_OBSERVATION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS agent_observations (
    id TEXT PRIMARY KEY,
    correlation_id TEXT NOT NULL,
    agent_type TEXT NOT NULL,
    session_id_hash TEXT NULL,
    event_type TEXT NOT NULL,
    tool_name TEXT NULL,
    summary TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_observations_scope_created
ON agent_observations (correlation_id, created_at DESC);

CREATE TABLE IF NOT EXISTS repository_snapshots (
    workspace_id TEXT PRIMARY KEY,
    digest TEXT NOT NULL,
    summary TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

POSTGRES_OBSERVATION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS agent_observations (
    id TEXT PRIMARY KEY,
    correlation_id TEXT NOT NULL,
    agent_type TEXT NOT NULL,
    session_id_hash TEXT NULL,
    event_type TEXT NOT NULL,
    tool_name TEXT NULL,
    summary TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_observations_scope_created
ON agent_observations (correlation_id, created_at DESC);

CREATE TABLE IF NOT EXISTS repository_snapshots (
    workspace_id TEXT PRIMARY KEY,
    digest TEXT NOT NULL,
    summary TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
"""

# ---------------------------------------------------------------------------
# SQLite — text-typed timestamps (ISO-8601 strings).
# ---------------------------------------------------------------------------

SQLITE_SCHEMA_SQL = (
    """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 1,
    run_after TEXT NULL,
    idempotency_key TEXT NULL,
    correlation_id TEXT NOT NULL,
    last_error TEXT NULL,
    leased_by TEXT NULL,
    lease_expires_at TEXT NULL,
    heartbeat_at TEXT NULL,
    started_at TEXT NULL,
    depends_on_json TEXT NOT NULL DEFAULT '[]',
    resource_key TEXT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_claimable
ON jobs (status, run_after, created_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_active_idempotency_key
ON jobs (idempotency_key)
WHERE idempotency_key IS NOT NULL
    AND status IN ('pending', 'running', 'retry_scheduled');

-- Phase 9.5: index for audit_log / list_tasks / resume_session correlation_id lookups.
CREATE INDEX IF NOT EXISTS idx_jobs_correlation_id
ON jobs (correlation_id);

CREATE TABLE IF NOT EXISTS job_events (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NULL,
    metadata_json TEXT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);

CREATE INDEX IF NOT EXISTS idx_job_events_job_created
ON job_events (job_id, created_at);

-- Phase 9.5: index for audit_log time-range queries spanning all jobs.
CREATE INDEX IF NOT EXISTS idx_job_events_created_at
ON job_events (created_at);

-- Delta-context ledger. Unlike job_events, rows are never deleted. AUTOINCREMENT
-- prevents revision reuse even after operators permanently delete a task and its
-- audit events. Each row stores the task state as it existed at that revision so
-- paginated deltas never expose a later state early.
CREATE TABLE IF NOT EXISTS context_revisions (
    revision INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NULL UNIQUE,
    job_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 1,
    last_error TEXT NULL,
    run_after TEXT NULL,
    depends_on_json TEXT NOT NULL DEFAULT '[]',
    resource_key TEXT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_context_revisions_correlation_revision
ON context_revisions (correlation_id, revision);

CREATE TRIGGER IF NOT EXISTS trg_job_events_context_revision
AFTER INSERT ON job_events
BEGIN
    INSERT OR IGNORE INTO context_revisions (
        event_id, job_id, task_type, correlation_id, event_type,
        status, payload_json, attempt, max_attempts, last_error,
        run_after, depends_on_json, resource_key, created_at
    )
    SELECT
        NEW.id, NEW.job_id, j.type, j.correlation_id, NEW.event_type,
        j.status, j.payload_json, j.attempt, j.max_attempts, j.last_error,
        j.run_after, j.depends_on_json, j.resource_key, NEW.created_at
    FROM jobs AS j
    WHERE j.id = NEW.job_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_jobs_context_delete
BEFORE DELETE ON jobs
BEGIN
    INSERT INTO context_revisions (
        event_id, job_id, task_type, correlation_id, event_type,
        status, payload_json, attempt, max_attempts, last_error,
        run_after, depends_on_json, resource_key, created_at
    )
    VALUES (
        NULL, OLD.id, OLD.type, OLD.correlation_id, 'job_deleted',
        'deleted', OLD.payload_json, OLD.attempt, OLD.max_attempts, OLD.last_error,
        OLD.run_after, OLD.depends_on_json, OLD.resource_key,
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    );
END;

-- Existing databases cannot reconstruct historical intermediate states. Backfill
-- one exact current-state snapshot per job; future events are captured by the
-- trigger above. The synthetic event id makes initialization idempotent.
INSERT OR IGNORE INTO context_revisions (
    event_id, job_id, task_type, correlation_id, event_type,
    status, payload_json, attempt, max_attempts, last_error,
    run_after, depends_on_json, resource_key, created_at
)
SELECT
    'backfill:' || j.id, j.id, j.type, j.correlation_id, 'state_backfilled',
    j.status, j.payload_json, j.attempt, j.max_attempts, j.last_error,
    j.run_after, j.depends_on_json, j.resource_key, j.updated_at
FROM jobs AS j
WHERE NOT EXISTS (
    SELECT 1 FROM context_revisions AS c WHERE c.job_id = j.id
)
ORDER BY j.rowid ASC;

-- Phase M4: agent registry — track which agents are online and what they can do.
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    capabilities_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    registered_at TEXT NOT NULL,
    last_heartbeat_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agents_status
ON agents (status, last_heartbeat_at);
"""
    + SQLITE_OBSERVATION_SCHEMA_SQL
)

# ---------------------------------------------------------------------------
# PostgreSQL — native TIMESTAMPTZ columns.
# ---------------------------------------------------------------------------

POSTGRES_SCHEMA_SQL = (
    """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 1,
    run_after TIMESTAMPTZ NULL,
    idempotency_key TEXT NULL,
    correlation_id TEXT NOT NULL,
    last_error TEXT NULL,
    leased_by TEXT NULL,
    lease_expires_at TIMESTAMPTZ NULL,
    heartbeat_at TIMESTAMPTZ NULL,
    started_at TIMESTAMPTZ NULL,
    depends_on_json TEXT NOT NULL DEFAULT '[]',
    resource_key TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_claimable
ON jobs (status, run_after, created_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_active_idempotency_key
ON jobs (idempotency_key)
WHERE idempotency_key IS NOT NULL
    AND status IN ('pending', 'running', 'retry_scheduled');

CREATE TABLE IF NOT EXISTS job_events (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NULL,
    metadata_json TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);

CREATE INDEX IF NOT EXISTS idx_job_events_job_created
ON job_events (job_id, created_at);

-- Reserved monotonic ledger for backend parity. resume_delta remains SQLite-only
-- until the PostgreSQL MCP path exposes the same compact query implementation.
CREATE TABLE IF NOT EXISTS context_revisions (
    revision BIGSERIAL PRIMARY KEY,
    event_id TEXT NULL UNIQUE,
    job_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 1,
    last_error TEXT NULL,
    run_after TIMESTAMPTZ NULL,
    depends_on_json TEXT NOT NULL DEFAULT '[]',
    resource_key TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_context_revisions_correlation_revision
ON context_revisions (correlation_id, revision);

-- Phase M4: agent registry.
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    capabilities_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    registered_at TIMESTAMPTZ NOT NULL,
    last_heartbeat_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agents_status
ON agents (status, last_heartbeat_at);
"""
    + POSTGRES_OBSERVATION_SCHEMA_SQL
)

# ---------------------------------------------------------------------------
# Column migrations for pre-existing databases.
#
# ``CREATE TABLE IF NOT EXISTS`` does NOT add columns to a table that already
# exists, so databases created by older versions of djobs need these columns
# back-filled. Each entry is (column_name, ALTER fragment). The fragment is
# spliced into a backend-appropriate ``ALTER TABLE jobs ADD COLUMN ...``.
# ---------------------------------------------------------------------------

# (column_name, column_definition)
JOBS_COLUMN_MIGRATIONS: list[tuple[str, str]] = [
    ("depends_on_json", "depends_on_json TEXT NOT NULL DEFAULT '[]'"),
    ("resource_key", "resource_key TEXT NULL"),
]


def apply_sqlite_column_migrations(connection: sqlite3.Connection) -> None:
    """Add post-initial columns to a pre-existing SQLite ``jobs`` table."""

    existing = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()}
    for column, definition in JOBS_COLUMN_MIGRATIONS:
        if column not in existing:
            connection.execute(f"ALTER TABLE jobs ADD COLUMN {definition}")


def apply_postgres_column_migrations(cursor: Any) -> None:
    """Add post-initial columns to a pre-existing PostgreSQL ``jobs`` table."""

    for _column, definition in JOBS_COLUMN_MIGRATIONS:
        cursor.execute(f"ALTER TABLE jobs ADD COLUMN IF NOT EXISTS {definition}")
