# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2022 Tulir Asokan
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
from mautrix.util.async_db import Connection, Scheme

latest_version = 13


async def create_latest_tables(conn: Connection, scheme: Scheme) -> int:
    await conn.execute(
        """CREATE TABLE "user" (
            mxid TEXT   PRIMARY KEY,
            tgid BIGINT UNIQUE,
            tg_username    TEXT,
            tg_phone       TEXT,
            is_bot         BOOLEAN NOT NULL DEFAULT false,
            is_premium     BOOLEAN NOT NULL DEFAULT false,
            saved_contacts INTEGER NOT NULL DEFAULT 0
        )"""
    )
    await conn.execute(
        """CREATE TABLE portal (
            tgid        BIGINT,
            tg_receiver BIGINT,
            peer_type   TEXT NOT NULL,
            mxid        TEXT UNIQUE,
            avatar_url  TEXT,
            encrypted   BOOLEAN NOT NULL DEFAULT false,
            username    TEXT,
            title       TEXT,
            about       TEXT,
            photo_id    TEXT,
            name_set    BOOLEAN NOT NULL DEFAULT false,
            avatar_set  BOOLEAN NOT NULL DEFAULT false,
            megagroup   BOOLEAN,
            config      jsonb,

            first_event_id    TEXT,
            next_batch_id     TEXT,
            base_insertion_id TEXT,

            sponsored_event_id     TEXT,
            sponsored_event_ts     BIGINT,
            sponsored_msg_random_id bytea,

            PRIMARY KEY (tgid, tg_receiver)
        )"""
    )
    await conn.execute(
        """CREATE TABLE message (
            mxid         TEXT   NOT NULL,
            mx_room      TEXT   NOT NULL,
            tgid         BIGINT,
            tg_space     BIGINT,
            edit_index   INTEGER,
            redacted     BOOLEAN NOT NULL DEFAULT false,
            content_hash bytea,
            sender_mxid  TEXT,
            sender       BIGINT,
            PRIMARY KEY (tgid, tg_space, edit_index),
            UNIQUE (mxid, mx_room, tg_space)
        )"""
    )
    await conn.execute(
        """CREATE TABLE reaction (
            mxid      TEXT NOT NULL,
            mx_room   TEXT NOT NULL,
            msg_mxid  TEXT NOT NULL,
            tg_sender BIGINT,
            reaction  TEXT NOT NULL,

            PRIMARY KEY (msg_mxid, mx_room, tg_sender, reaction),
            UNIQUE (mxid, mx_room)
        )"""
    )
    await conn.execute(
        """CREATE TABLE disappearing_message (
            room_id             TEXT,
            event_id            TEXT,
            expiration_seconds  BIGINT,
            expiration_ts       BIGINT,

            PRIMARY KEY (room_id, event_id)
        )"""
    )
    await conn.execute(
        """CREATE TABLE puppet (
            id BIGINT PRIMARY KEY,

            is_registered BOOLEAN NOT NULL DEFAULT false,

            displayname         TEXT,
            displayname_source  BIGINT,
            displayname_contact BOOLEAN NOT NULL DEFAULT true,
            displayname_quality INTEGER NOT NULL DEFAULT 0,
            disable_updates     BOOLEAN NOT NULL DEFAULT false,
            username            TEXT,
            phone               TEXT,
            photo_id            TEXT,
            avatar_url          TEXT,
            name_set            BOOLEAN NOT NULL DEFAULT false,
            avatar_set          BOOLEAN NOT NULL DEFAULT false,
            is_bot              BOOLEAN,
            is_channel          BOOLEAN NOT NULL DEFAULT false,
            is_premium          BOOLEAN NOT NULL DEFAULT false,

            access_token TEXT,
            custom_mxid  TEXT,
            next_batch   TEXT,
            base_url     TEXT
        )"""
    )
    await conn.execute("CREATE INDEX puppet_username_idx ON puppet(LOWER(username))")
    await conn.execute(
        """CREATE TABLE telegram_file (
            id              TEXT PRIMARY KEY,
            mxc             TEXT NOT NULL,
            mime_type       TEXT,
            was_converted   BOOLEAN NOT NULL DEFAULT false,
            timestamp       BIGINT  NOT NULL DEFAULT 0,
            size            BIGINT,
            width           INTEGER,
            height          INTEGER,
            thumbnail       TEXT,
            decryption_info jsonb,
            FOREIGN KEY (thumbnail) REFERENCES telegram_file(id)
                ON UPDATE CASCADE ON DELETE SET NULL
        )"""
    )
    await conn.execute("CREATE INDEX telegram_file_mxc_idx ON telegram_file(mxc)")
    await conn.execute(
        """CREATE TABLE bot_chat (
            id   BIGINT PRIMARY KEY,
            type TEXT NOT NULL
        )"""
    )
    await conn.execute(
        """CREATE TABLE user_portal (
            "user"          BIGINT,
            portal          BIGINT,
            portal_receiver BIGINT,
            PRIMARY KEY ("user", portal, portal_receiver),
            FOREIGN KEY ("user") REFERENCES "user"(tgid) ON DELETE CASCADE ON UPDATE CASCADE,
            FOREIGN KEY (portal, portal_receiver) REFERENCES portal(tgid, tg_receiver)
                 ON DELETE CASCADE ON UPDATE CASCADE
        )"""
    )
    await conn.execute(
        """CREATE TABLE contact (
            "user"  BIGINT,
            contact BIGINT,
            PRIMARY KEY ("user", contact),
            FOREIGN KEY ("user")  REFERENCES "user"(tgid) ON DELETE CASCADE ON UPDATE CASCADE,
            FOREIGN KEY (contact) REFERENCES puppet(id)   ON DELETE CASCADE ON UPDATE CASCADE
        )"""
    )
    await conn.execute(
        """CREATE TABLE telethon_sessions (
            session_id     TEXT PRIMARY KEY,
            dc_id          INTEGER,
            server_address TEXT,
            port           INTEGER,
            auth_key       bytea
        )"""
    )
    await conn.execute(
        """CREATE TABLE telethon_entities (
            session_id TEXT,
            id         BIGINT,
            hash       BIGINT NOT NULL,
            username   TEXT,
            phone      TEXT,
            name       TEXT,
            PRIMARY KEY (session_id, id)
        )"""
    )
    await conn.execute(
        """CREATE TABLE telethon_sent_files (
            session_id TEXT,
            md5_digest bytea,
            file_size  INTEGER,
            type       INTEGER,
            id         BIGINT,
            hash       BIGINT,
            PRIMARY KEY (session_id, md5_digest, file_size, type)
        )"""
    )
    await conn.execute(
        """CREATE TABLE telethon_update_state (
            session_id   TEXT,
            entity_id    BIGINT,
            pts          BIGINT,
            qts          BIGINT,
            date         BIGINT,
            seq          BIGINT,
            unread_count INTEGER,
            PRIMARY KEY (session_id, entity_id)
        )"""
    )
    gen = ""
    if scheme in (Scheme.POSTGRES, Scheme.COCKROACH):
        gen = "GENERATED ALWAYS AS IDENTITY"
    await conn.execute(
        f"""
        CREATE TABLE backfill_queue (
            queue_id            INTEGER PRIMARY KEY {gen},
            user_mxid           TEXT,
            priority            INTEGER NOT NULL,
            portal_tgid         BIGINT,
            portal_tg_receiver  BIGINT,
            messages_per_batch  INTEGER NOT NULL,
            post_batch_delay    INTEGER NOT NULL,
            max_batches         INTEGER NOT NULL,
            dispatch_time       TIMESTAMP,
            completed_at        TIMESTAMP,
            cooldown_timeout    TIMESTAMP,
            FOREIGN KEY (user_mxid) REFERENCES "user"(mxid) ON DELETE CASCADE ON UPDATE CASCADE,
            FOREIGN KEY (portal_tgid, portal_tg_receiver)
                REFERENCES portal(tgid, tg_receiver) ON DELETE CASCADE
        )
        """
    )

    return latest_version
