-- Bounded, deduplicated diagnostics for fail-open hooks and memory adapters.
CREATE TABLE IF NOT EXISTS djobs_diagnostics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    component TEXT NOT NULL,
    error_type TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    last_message TEXT NOT NULL,
    context_json TEXT NOT NULL DEFAULT '{}',
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(component, fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_djobs_diagnostics_last_seen
ON djobs_diagnostics (last_seen_at DESC);

-- PostgreSQL (TIMESTAMPTZ variant)
-- CREATE TABLE IF NOT EXISTS djobs_diagnostics (
--     id BIGSERIAL PRIMARY KEY,
--     component TEXT NOT NULL,
--     error_type TEXT NOT NULL,
--     fingerprint TEXT NOT NULL,
--     last_message TEXT NOT NULL,
--     context_json TEXT NOT NULL DEFAULT '{}',
--     occurrence_count INTEGER NOT NULL DEFAULT 1,
--     first_seen_at TIMESTAMPTZ NOT NULL,
--     last_seen_at TIMESTAMPTZ NOT NULL,
--     UNIQUE(component, fingerprint)
-- );
--
-- CREATE INDEX IF NOT EXISTS idx_djobs_diagnostics_last_seen
-- ON djobs_diagnostics (last_seen_at DESC);
