ALTER TABLE observability.tool_invocations
    ADD COLUMN IF NOT EXISTS session_id VARCHAR(255);

ALTER TABLE observability.tool_invocations
    ADD COLUMN IF NOT EXISTS attempt INTEGER DEFAULT 0;

ALTER TABLE observability.agent_errors
    ADD COLUMN IF NOT EXISTS session_id VARCHAR(255);

ALTER TABLE observability.agent_errors
    ADD COLUMN IF NOT EXISTS attempt INTEGER DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_obs_tool_invocations_session_created
    ON observability.tool_invocations (session_id, created_at DESC);
