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


@upgrade_table.register(description="Add type for backfill queue items")
async def upgrade_v16(conn: Connection, scheme: Scheme) -> None:
    await conn.execute(
        "ALTER TABLE backfill_queue ADD COLUMN type TEXT NOT NULL DEFAULT 'historical'"
    )
    await conn.execute("ALTER TABLE backfill_queue ADD COLUMN extra_data jsonb")
    if scheme != Scheme.SQLITE:
        await conn.execute("ALTER TABLE backfill_queue ALTER COLUMN type DROP DEFAULT")
