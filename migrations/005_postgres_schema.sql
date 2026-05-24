-- PostgreSQL schema for distributed-job-system.
-- Equivalent to the SQLite schema but uses TIMESTAMPTZ and native PG types.

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
