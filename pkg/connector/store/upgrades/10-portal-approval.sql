-- v10 (compatible with v2+): Store Telegram portal approval state

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
