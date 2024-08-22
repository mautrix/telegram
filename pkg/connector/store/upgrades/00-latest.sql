-- v0 -> v1: Latest revision

CREATE TABLE telegram_user_state (
    user_id BIGINT NOT NULL PRIMARY KEY,
    pts     BIGINT NOT NULL,
    qts     BIGINT NOT NULL,
    date    BIGINT NOT NULL,
    seq     BIGINT NOT NULL
);

CREATE TABLE telegram_channel_state (
    user_id    BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    pts        BIGINT NOT NULL,

    PRIMARY KEY (user_id, channel_id)
);

CREATE INDEX telegram_channel_state_user_id_idx ON telegram_channel_state (user_id);

CREATE TABLE telegram_access_hash (
    user_id     BIGINT NOT NULL,
    entity_id   BIGINT NOT NULL,
    access_hash BIGINT NOT NULL,

    PRIMARY KEY (user_id, entity_id)
);

CREATE TABLE telegram_username (
    username  TEXT   NOT NULL,
    entity_id BIGINT NOT NULL,

    PRIMARY KEY (username)
);

CREATE INDEX telegram_username_entity_idx ON telegram_username (entity_id);
CREATE INDEX telegram_username_username_idx ON telegram_username (LOWER(username));

CREATE TABLE telegram_phone_number (
    phone_number  TEXT   NOT NULL,
    entity_id     BIGINT NOT NULL,

    PRIMARY KEY (phone_number)
);

CREATE INDEX telegram_phone_number_entity_idx ON telegram_phone_number (entity_id);

CREATE TABLE telegram_file (
    id        TEXT PRIMARY KEY,
    mxc       TEXT NOT NULL,
    mime_type TEXT,
    size      BIGINT
);
