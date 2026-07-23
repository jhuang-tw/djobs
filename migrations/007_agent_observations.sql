-- SQLite repository observations for client-neutral cross-agent context.
-- These tables are separate from jobs and never change task ownership.

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
