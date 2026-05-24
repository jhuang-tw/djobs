-- Phase 6: Add correlation_id and started_at columns for observability.
ALTER TABLE jobs ADD COLUMN correlation_id TEXT NULL;
ALTER TABLE jobs ADD COLUMN started_at TEXT NULL;
