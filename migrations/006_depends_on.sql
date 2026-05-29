-- M2: task dependencies (DAG). A job becomes claimable only after every
-- job id listed in depends_on_json has reached the 'succeeded' status.
-- Stored as a JSON array of job ids; empty array '[]' means no dependencies.

-- SQLite
ALTER TABLE jobs ADD COLUMN depends_on_json TEXT NOT NULL DEFAULT '[]';

-- PostgreSQL (idempotent variant)
-- ALTER TABLE jobs ADD COLUMN IF NOT EXISTS depends_on_json TEXT NOT NULL DEFAULT '[]';
