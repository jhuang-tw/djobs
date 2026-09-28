-- Optional typed memory v1; no changes to raw observations or tasks.


CREATE TABLE IF NOT EXISTS djobs_memory_schema (
    component TEXT PRIMARY KEY,
    version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_artifacts (
    id TEXT PRIMARY KEY,
    repo_family_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    authority TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT NOT NULL,
    abstract TEXT NOT NULL,
    overview TEXT NOT NULL,
    details_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    created_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    content_hash TEXT NOT NULL,
    proposal_hash TEXT NOT NULL,
    source_count INTEGER NOT NULL CHECK (source_count BETWEEN 1 AND 16)
);
CREATE INDEX IF NOT EXISTS idx_memory_artifacts_family
    ON memory_artifacts(repo_family_id, kind, status, valid_from);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_artifact_proposals
    ON memory_artifacts(repo_family_id, proposal_hash);
CREATE TABLE IF NOT EXISTS memory_artifact_sources (
    artifact_id TEXT NOT NULL REFERENCES memory_artifacts(id) ON DELETE CASCADE,
    source_kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    observation_id TEXT REFERENCES agent_observations(id) ON DELETE SET NULL,
    source_artifact_id TEXT REFERENCES memory_artifacts(id) ON DELETE SET NULL,
    PRIMARY KEY (artifact_id, source_kind, source_id)
);
CREATE INDEX IF NOT EXISTS idx_memory_artifact_source_observation
    ON memory_artifact_sources(observation_id);
CREATE INDEX IF NOT EXISTS idx_memory_artifact_source_artifact
    ON memory_artifact_sources(source_artifact_id);
CREATE TABLE IF NOT EXISTS memory_relations (
    source_id TEXT NOT NULL REFERENCES memory_artifacts(id) ON DELETE CASCADE,
    target_id TEXT NOT NULL REFERENCES memory_artifacts(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    PRIMARY KEY (source_id, target_id, kind),
    CHECK (source_id != target_id)
);
CREATE TABLE IF NOT EXISTS memory_reviews (
    id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL REFERENCES memory_artifacts(id) ON DELETE CASCADE,
    decision TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    policy TEXT NOT NULL,
    binding_hash TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

INSERT INTO djobs_memory_schema(component,version) VALUES ('artifacts',1)
ON CONFLICT(component) DO NOTHING;
