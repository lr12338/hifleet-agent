CREATE TABLE IF NOT EXISTS observability.chat_debug_sessions (
    id BIGSERIAL PRIMARY KEY,
    session_key VARCHAR(255) NOT NULL,
    title VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL,
    meta_session_id VARCHAR(255),
    user_id VARCHAR(255),
    source_channel VARCHAR(64),
    model VARCHAR(128),
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_obs_chat_debug_sessions_key
    ON observability.chat_debug_sessions (session_key);
CREATE INDEX IF NOT EXISTS idx_obs_chat_debug_sessions_updated
    ON observability.chat_debug_sessions (updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_obs_chat_debug_sessions_meta_session
    ON observability.chat_debug_sessions (meta_session_id, updated_at DESC);
