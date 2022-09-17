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

from . import upgrade_table


@upgrade_table.register(description="Allow multiple reactions from the same user")
async def upgrade_v13(conn: Connection, scheme: Scheme) -> None:
    await conn.execute("CREATE INDEX telegram_file_mxc_idx ON telegram_file(mxc)")
    await conn.execute('ALTER TABLE "user" ADD COLUMN is_premium BOOLEAN NOT NULL DEFAULT false')
    await conn.execute("ALTER TABLE puppet ADD COLUMN is_premium BOOLEAN NOT NULL DEFAULT false")
    if scheme == Scheme.POSTGRES:
        await conn.execute(
            """
            ALTER TABLE reaction
                DROP CONSTRAINT reaction_pkey,
                ADD CONSTRAINT reaction_pkey PRIMARY KEY (msg_mxid, mx_room, tg_sender, reaction)
            """
        )
    else:
        await conn.execute(
            """CREATE TABLE new_reaction (
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
            """
            INSERT INTO new_reaction (mxid, mx_room, msg_mxid, tg_sender, reaction)
            SELECT mxid, mx_room, msg_mxid, tg_sender, reaction FROM reaction
            """
        )
        await conn.execute("DROP TABLE reaction")
        await conn.execute("ALTER TABLE new_reaction RENAME TO reaction")
