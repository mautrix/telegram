-- v0 -> v1: Latest revision

-- TODO do I need to have bridge ID here?
CREATE TABLE telegram_session (
    user_id      INTEGER PRIMARY KEY,
    session_data BYTEA NOT NULL
);
