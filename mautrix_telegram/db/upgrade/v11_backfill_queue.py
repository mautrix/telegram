# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2022 Tulir Asokan, Sumner Evans
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


@upgrade_table.register(description="Add the backfill queue table")
async def upgrade_v11(conn: Connection, scheme: Scheme) -> None:
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
