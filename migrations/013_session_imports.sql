-- Native quarantined session imports; no raw memory/task changes.

CREATE TABLE IF NOT EXISTS djobs_memory_schema (component TEXT PRIMARY KEY, version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS memory_session_imports (
    id TEXT PRIMARY KEY,
    repo_family_id TEXT NOT NULL,
    source_harness TEXT NOT NULL,
    source_format_version TEXT NOT NULL,
    source_session_id TEXT NOT NULL,
    source_content_hash TEXT NOT NULL,
    adapter_version TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    redaction_version TEXT NOT NULL,
    source_path_fingerprint TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    selection_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('imported_unverified','reviewed_reference','rejected')),
    payload_json TEXT NOT NULL,
    review_json TEXT NULL,
    UNIQUE(repo_family_id, selection_hash)
);
INSERT INTO djobs_memory_schema(component,version) VALUES ('session_imports',1)
ON CONFLICT(component) DO NOTHING;
