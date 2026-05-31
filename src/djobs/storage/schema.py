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
# SQLite — text-typed timestamps (ISO-8601 strings).
# ---------------------------------------------------------------------------

SQLITE_SCHEMA_SQL = """
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

# ---------------------------------------------------------------------------
# PostgreSQL — native TIMESTAMPTZ columns.
# ---------------------------------------------------------------------------

POSTGRES_SCHEMA_SQL = """
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
