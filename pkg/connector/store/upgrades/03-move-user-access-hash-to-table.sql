-- v3: Move the user access hash to a table so it can be per-user

CREATE TABLE telegram_user_metadata (
    receiver_id INTEGER,
    user_id     INTEGER,

    access_hash INTEGER NOT NULL,
    username    TEXT,

    PRIMARY KEY (receiver_id, user_id)
);

INSERT INTO telegram_user_metadata (receiver_id, user_id, access_hash, username)
SELECT ul.id, g.id, g.metadata->>'access_hash', g.metadata->>'username'
FROM user_login ul, ghost g;
