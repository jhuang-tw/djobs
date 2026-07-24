-- Optional SQLite FTS5 search index for repository-scoped passive memory.
-- Runtime initialization treats FTS5 as an optimization and falls back to
-- bounded token/substring relevance when the local SQLite build omits FTS5.
CREATE VIRTUAL TABLE IF NOT EXISTS agent_observations_fts USING fts5(
    observation_id UNINDEXED,
    correlation_id UNINDEXED,
    event_type,
    tool_name,
    summary,
    tokenize='unicode61 remove_diacritics 2'
);

INSERT INTO agent_observations_fts (
    observation_id, correlation_id, event_type, tool_name, summary
)
SELECT o.id, o.correlation_id, o.event_type, COALESCE(o.tool_name, ''), o.summary
FROM agent_observations AS o
WHERE NOT EXISTS (
    SELECT 1 FROM agent_observations_fts AS f WHERE f.observation_id = o.id
);

CREATE TRIGGER IF NOT EXISTS trg_agent_observations_fts_insert
AFTER INSERT ON agent_observations
BEGIN
    INSERT INTO agent_observations_fts (
        observation_id, correlation_id, event_type, tool_name, summary
    ) VALUES (NEW.id, NEW.correlation_id, NEW.event_type, COALESCE(NEW.tool_name, ''), NEW.summary);
END;

CREATE TRIGGER IF NOT EXISTS trg_agent_observations_fts_delete
AFTER DELETE ON agent_observations
BEGIN
    DELETE FROM agent_observations_fts WHERE observation_id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_agent_observations_fts_update
AFTER UPDATE ON agent_observations
BEGIN
    DELETE FROM agent_observations_fts WHERE observation_id = OLD.id;
    INSERT INTO agent_observations_fts (
        observation_id, correlation_id, event_type, tool_name, summary
    ) VALUES (NEW.id, NEW.correlation_id, NEW.event_type, COALESCE(NEW.tool_name, ''), NEW.summary);
END;
