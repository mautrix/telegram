# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2021 Tulir Asokan
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
from __future__ import annotations

from asyncpg import Connection

from . import upgrade_table

legacy_version_query = "SELECT version_num FROM alembic_version"
last_legacy_version = "bfc0a39bfe02"


def table_exists(scheme: str, name: str) -> str:
    if scheme == "sqlite":
        return f"SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='{name}')"
    elif scheme == "postgres":
        return f"SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name='{name}')"
    raise RuntimeError("unsupported database scheme")


@upgrade_table.register(description="Initial asyncpg revision")
async def upgrade_v1(conn: Connection, scheme: str) -> None:
    is_legacy = await conn.fetchval(table_exists(scheme, "alembic_version"))
    if is_legacy:
        await migrate_legacy_to_v1(conn, scheme)
    else:
        await create_v1_tables(conn)


async def drop_constraints(conn: Connection, table: str, contype: str) -> None:
    q = (
        "SELECT conname FROM pg_constraint con INNER JOIN pg_class rel ON rel.oid=con.conrelid "
        f"WHERE rel.relname='{table}' AND contype='{contype}'"
    )
    names = [row["conname"] for row in await conn.fetch(q)]
    drops = ", ".join(f"DROP CONSTRAINT {name}" for name in names)
    await conn.execute(f"ALTER TABLE {table} {drops}")


async def migrate_legacy_to_v1(conn: Connection, scheme: str) -> None:
    legacy_version = await conn.fetchval(legacy_version_query)
    if legacy_version != last_legacy_version:
        raise RuntimeError(
            "Legacy database is not on last version. "
            "Please upgrade the old database with alembic or drop it completely first."
        )
    if scheme != "sqlite":
        await drop_constraints(conn, "contact", contype="f")
        await conn.execute(
            """
            ALTER TABLE contact
              ADD CONSTRAINT contact_user_fkey FOREIGN KEY (contact) REFERENCES puppet(id)
                ON DELETE CASCADE ON UPDATE CASCADE,
              ADD CONSTRAINT contact_contact_fkey FOREIGN KEY ("user") REFERENCES "user"(tgid)
                ON DELETE CASCADE ON UPDATE CASCADE
            """
        )
        await drop_constraints(conn, "telethon_sessions", contype="p")
        await conn.execute(
            """
            ALTER TABLE telethon_sessions
              ADD CONSTRAINT telethon_sessions_pkey PRIMARY KEY (session_id)
            """
        )
        await drop_constraints(conn, "telegram_file", contype="f")
        await conn.execute(
            """
            ALTER TABLE telegram_file
              ADD CONSTRAINT fk_file_thumbnail
                FOREIGN KEY (thumbnail) REFERENCES telegram_file(id)
                ON UPDATE CASCADE ON DELETE SET NULL
            """
        )
        await conn.execute("ALTER TABLE puppet ALTER COLUMN id DROP IDENTITY IF EXISTS")
        await conn.execute("ALTER TABLE puppet ALTER COLUMN id DROP DEFAULT")
        await conn.execute("DROP SEQUENCE IF EXISTS puppet_id_seq")
        await conn.execute("ALTER TABLE bot_chat ALTER COLUMN id DROP IDENTITY IF EXISTS")
        await conn.execute("ALTER TABLE bot_chat ALTER COLUMN id DROP DEFAULT")
        await conn.execute("DROP SEQUENCE IF EXISTS bot_chat_id_seq")
        await conn.execute("ALTER TABLE portal ALTER COLUMN config TYPE jsonb USING config::jsonb")
        await conn.execute(
            "ALTER TABLE telegram_file ALTER COLUMN decryption_info TYPE jsonb "
            "USING decryption_info::jsonb"
        )
        await varchar_to_text(conn)
    else:
        await conn.execute(
            """CREATE TABLE telethon_sessions_new (
                session_id     TEXT PRIMARY KEY,
                dc_id          INTEGER,
                server_address TEXT,
                port           INTEGER,
                auth_key       bytea
            )"""
        )
        await conn.execute(
            """
            INSERT INTO telethon_sessions_new (session_id, dc_id, server_address, port, auth_key)
            SELECT session_id, dc_id, server_address, port, auth_key FROM telethon_sessions
            """
        )
        await conn.execute("DROP TABLE telethon_sessions")
        await conn.execute("ALTER TABLE telethon_sessions_new RENAME TO telethon_sessions")

    await update_state_store(conn, scheme)
    await conn.execute('ALTER TABLE "user" ADD COLUMN is_bot BOOLEAN NOT NULL DEFAULT false')
    await conn.execute("ALTER TABLE puppet RENAME COLUMN matrix_registered TO is_registered")
    await conn.execute("DROP TABLE telethon_version")
    await conn.execute("DROP TABLE alembic_version")


async def update_state_store(conn: Connection, scheme: str) -> None:
    # The Matrix state store already has more or less the correct schema, so set the version
    await conn.execute("CREATE TABLE mx_version (version INTEGER PRIMARY KEY)")
    await conn.execute("INSERT INTO mx_version (version) VALUES (2)")
    await conn.execute("UPDATE mx_user_profile SET membership='LEAVE' WHERE membership='LEFT'")
    if scheme != "sqlite":
        # Also add the membership type on postgres
        await conn.execute(
            "CREATE TYPE membership AS ENUM ('join', 'leave', 'invite', 'ban', 'knock')"
        )
        await conn.execute(
            "ALTER TABLE mx_user_profile ALTER COLUMN membership TYPE membership "
            "USING LOWER(membership)::membership"
        )
    else:
        # On SQLite there's no custom type, but we still want to lowercase everything
        await conn.execute("UPDATE mx_user_profile SET membership=LOWER(membership)")


async def varchar_to_text(conn: Connection) -> None:
    columns_to_adjust = {
        "user": ("mxid", "tg_username", "tg_phone"),
        "portal": (
            "peer_type",
            "mxid",
            "username",
            "title",
            "about",
            "photo_id",
            "avatar_url",
            "config",
        ),
        "message": ("mxid", "mx_room"),
        "puppet": (
            "displayname",
            "username",
            "photo_id",
            "access_token",
            "custom_mxid",
            "next_batch",
            "base_url",
        ),
        "bot_chat": ("type",),
        "telegram_file": ("id", "mxc", "mime_type", "thumbnail"),
        # Phone is a bigint in the old schema, which is safe, but we don't do math on it,
        # so let's change it to a string
        "telethon_entities": ("session_id", "username", "name", "phone"),
        "telethon_sent_files": ("session_id",),
        "telethon_sessions": ("session_id", "server_address"),
        "telethon_update_state": ("session_id",),
        "mx_room_state": ("room_id",),
        "mx_user_profile": ("room_id", "user_id", "displayname", "avatar_url"),
    }
    for table, columns in columns_to_adjust.items():
        for column in columns:
            await conn.execute(f'ALTER TABLE "{table}" ALTER COLUMN {column} TYPE TEXT')


async def create_v1_tables(conn: Connection) -> None:
    await conn.execute(
        """CREATE TABLE "user" (
            mxid TEXT   PRIMARY KEY,
            tgid BIGINT UNIQUE,
            tg_username    TEXT,
            tg_phone       TEXT,
            is_bot         BOOLEAN NOT NULL DEFAULT false,
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
            megagroup   BOOLEAN,
            config      jsonb,
            PRIMARY KEY (tgid, tg_receiver)
        )"""
    )
    await conn.execute(
        """CREATE TABLE message (
            mxid       TEXT,
            mx_room    TEXT,
            tgid       BIGINT NOT NULL,
            tg_space   BIGINT NOT NULL,
            edit_index INTEGER NOT NULL,
            redacted   BOOLEAN NOT NULL DEFAULT false,
            PRIMARY KEY (tgid, tg_space, edit_index),
            UNIQUE (mxid, mx_room, tg_space)
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
            photo_id            TEXT,
            is_bot              BOOLEAN,

            access_token TEXT,
            custom_mxid  TEXT,
            next_batch   TEXT,
            base_url     TEXT
        )"""
    )
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
