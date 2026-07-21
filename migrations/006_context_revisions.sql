-- SQLite monotonic revision ledger for delta-context recovery.
-- Apply to an existing database before using resume_delta.

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
