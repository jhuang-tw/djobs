-- Optional native retrieval component v1. Explicit reindex only.
-- PostgreSQL operators replace BLOB with BYTEA. No observation/task rows change.

CREATE TABLE IF NOT EXISTS djobs_memory_schema (
    component TEXT PRIMARY KEY,
    version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_embedding_indexes (
    repo_family_id TEXT PRIMARY KEY,
    identity_hash TEXT NOT NULL,
    identity_json TEXT NOT NULL,
    source_digest TEXT NOT NULL,
    record_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_embeddings (
    record_id TEXT NOT NULL REFERENCES agent_observations(id) ON DELETE CASCADE,
    repo_family_id TEXT NOT NULL,
    identity_hash TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    vector_bytes BLOB NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (record_id, repo_family_id)
);
CREATE INDEX IF NOT EXISTS idx_memory_embeddings_family
    ON memory_embeddings(repo_family_id, identity_hash);
CREATE TABLE IF NOT EXISTS memory_entity_links (
    record_id TEXT NOT NULL REFERENCES agent_observations(id) ON DELETE CASCADE,
    repo_family_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    PRIMARY KEY (record_id, repo_family_id, kind, value)
);
CREATE INDEX IF NOT EXISTS idx_memory_entities_family
    ON memory_entity_links(repo_family_id, kind, value);

INSERT INTO djobs_memory_schema(component,version) VALUES ('retrieval',1)
ON CONFLICT(component) DO NOTHING;
