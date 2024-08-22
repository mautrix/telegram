-- v0 -> v3: Latest revision

CREATE TABLE telegram_session (
    user_id      BIGINT PRIMARY KEY,
    session_data BYTEA NOT NULL
);

CREATE TABLE telegram_user_state (
    user_id BIGINT PRIMARY KEY,
    pts     BIGINT NOT NULL,
    qts     BIGINT NOT NULL,
    date    BIGINT NOT NULL,
    seq     BIGINT NOT NULL
);

CREATE TABLE telegram_channel_state (
    user_id    BIGINT,
    channel_id BIGINT,
    pts        BIGINT NOT NULL,

    PRIMARY KEY (user_id, channel_id)
);

CREATE INDEX idx_telegram_channel_state_user_id ON telegram_channel_state (user_id);

CREATE TABLE telegram_channel_access_hashes (
    user_id     BIGINT,
    channel_id  BIGINT,
    access_hash BIGINT NOT NULL,

    PRIMARY KEY (user_id, channel_id)
);

CREATE TABLE telegram_user_metadata (
    receiver_id BIGINT,
    user_id     BIGINT,

    access_hash BIGINT NOT NULL,
    username    TEXT,

    PRIMARY KEY (receiver_id, user_id)
);

CREATE TABLE telegram_file (
    id        TEXT PRIMARY KEY,
    mxc       TEXT NOT NULL,
    mime_type TEXT,
    size      BIGINT
);

-- TODO this will be unnecessary once the queries switch to reading telegram_user_metadata
CREATE INDEX idx_ghost_username ON ghost ((metadata->>'username'));
