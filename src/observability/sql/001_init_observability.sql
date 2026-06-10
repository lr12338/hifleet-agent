CREATE SCHEMA IF NOT EXISTS observability;

CREATE TABLE IF NOT EXISTS observability.api_calls (
    id BIGSERIAL PRIMARY KEY,
    run_id VARCHAR(255) NOT NULL,
    session_id VARCHAR(255),
    user_id VARCHAR(255),
    source_channel VARCHAR(64),
    route VARCHAR(128) NOT NULL,
    intent_hint VARCHAR(64),
    request_json JSONB,
    response_json JSONB,
    http_status_code INTEGER,
    status VARCHAR(64) NOT NULL,
    latency_ms INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_obs_api_calls_run_id
    ON observability.api_calls (run_id);
CREATE INDEX IF NOT EXISTS idx_obs_api_calls_session_created
    ON observability.api_calls (session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_obs_api_calls_user_created
    ON observability.api_calls (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_obs_api_calls_created
    ON observability.api_calls (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_obs_api_calls_status_created
    ON observability.api_calls (status, created_at DESC);

CREATE TABLE IF NOT EXISTS observability.tool_invocations (
    id BIGSERIAL PRIMARY KEY,
    run_id VARCHAR(255) NOT NULL,
    tool_name VARCHAR(128) NOT NULL,
    tool_args JSONB,
    tool_result JSONB,
    status VARCHAR(64) NOT NULL,
    code VARCHAR(128),
    message TEXT,
    retriable BOOLEAN DEFAULT FALSE,
    latency_ms INTEGER DEFAULT 0,
    source VARCHAR(128),
    layer_trace JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_obs_tool_invocations_run
    ON observability.tool_invocations (run_id);
CREATE INDEX IF NOT EXISTS idx_obs_tool_invocations_tool_created
    ON observability.tool_invocations (tool_name, created_at DESC);

CREATE TABLE IF NOT EXISTS observability.agent_errors (
    id BIGSERIAL PRIMARY KEY,
    run_id VARCHAR(255) NOT NULL,
    route VARCHAR(128),
    error_code VARCHAR(128) NOT NULL,
    error_message TEXT,
    stack_trace TEXT,
    error_category VARCHAR(128),
    node_name VARCHAR(128),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_obs_agent_errors_run
    ON observability.agent_errors (run_id);
CREATE INDEX IF NOT EXISTS idx_obs_agent_errors_code_created
    ON observability.agent_errors (error_code, created_at DESC);
