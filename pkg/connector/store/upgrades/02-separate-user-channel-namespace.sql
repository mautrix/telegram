-- v2: Separate users and channels into separate namespaces

ALTER TABLE telegram_access_hash RENAME TO telegram_access_hash_old;
ALTER TABLE telegram_username RENAME TO telegram_username_old;

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

INSERT INTO telegram_access_hash (user_id, entity_type, entity_id, access_hash)
SELECT user_id, 'user', entity_id, access_hash
FROM telegram_access_hash_old;

INSERT INTO telegram_access_hash (user_id, entity_type, entity_id, access_hash)
SELECT user_id, 'channel', entity_id, access_hash
FROM telegram_access_hash_old;

INSERT INTO telegram_username (username, entity_type, entity_id)
SELECT username, 'user', entity_id
FROM telegram_username_old;

DROP TABLE telegram_access_hash_old;
DROP table telegram_username_old;
