-- M4: agent registry. Tracks which agents are registered, what task types
-- they can handle (capabilities), free-form metadata (hostname, pid, version),
-- and liveness via last_heartbeat_at. Agents that stop heartbeating are
-- auto-marked offline so the queue knows who is actually available.

-- SQLite
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

-- PostgreSQL (TIMESTAMPTZ variant)
-- CREATE TABLE IF NOT EXISTS agents (
--     id TEXT PRIMARY KEY,
--     status TEXT NOT NULL,
--     capabilities_json TEXT NOT NULL DEFAULT '[]',
--     metadata_json TEXT NOT NULL DEFAULT '{}',
--     registered_at TIMESTAMPTZ NOT NULL,
--     last_heartbeat_at TIMESTAMPTZ NOT NULL
-- );
--
-- CREATE INDEX IF NOT EXISTS idx_agents_status
-- ON agents (status, last_heartbeat_at);
