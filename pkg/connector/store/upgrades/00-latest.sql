-- v0 -> v8 (compatible with v2+): Latest revision

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

CREATE TABLE telegram_access_hash (
    user_id     BIGINT NOT NULL,
    entity_type TEXT   NOT NULL,
    entity_id   BIGINT NOT NULL,
    access_hash BIGINT NOT NULL,

    PRIMARY KEY (user_id, entity_type, entity_id)
);

CREATE TABLE telegram_username (
    username    TEXT   NOT NULL,
    entity_type TEXT   NOT NULL,
    entity_id   BIGINT NOT NULL,

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
    size      BIGINT,
    width     INTEGER,
    height    INTEGER,
    timestamp BIGINT
);

CREATE INDEX telegram_file_mxc_idx ON telegram_file (mxc);

CREATE TABLE telegram_topic (
    channel_id BIGINT NOT NULL,
    topic_id   BIGINT NOT NULL,

    PRIMARY KEY (channel_id, topic_id)
);

CREATE TABLE telegram_portal_approval (
    approval_id     BIGINT NOT NULL PRIMARY KEY,
    user_id         BIGINT NOT NULL,
    portal_id       TEXT   NOT NULL,
    portal_receiver TEXT   NOT NULL,
    peer_type       TEXT   NOT NULL,
    entity_id       BIGINT NOT NULL,
    topic_id        BIGINT NOT NULL,
    title           TEXT   NOT NULL,
    username        TEXT   NOT NULL,
    status          TEXT   NOT NULL,
    last_event      TEXT   NOT NULL,
    created_ts      BIGINT NOT NULL,
    last_seen_ts    BIGINT NOT NULL,

    UNIQUE (user_id, portal_id, portal_receiver)
);

CREATE INDEX telegram_portal_approval_user_status_idx ON telegram_portal_approval (user_id, status, approval_id);
