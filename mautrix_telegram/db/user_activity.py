# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2019 Tulir Asokan
# Copyright (C) 2021 Tadeusz Sośnierz
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
from typing import TYPE_CHECKING, ClassVar
from asyncpg import Record
from attr import dataclass
from yarl import URL

import logging
import datetime
import time

from mautrix.types import SyncToken, UserID
from mautrix.util.async_db import Database
from mautrix.util.logging import TraceLogger
from ..types import TelegramID

fake_db = Database.create("") if TYPE_CHECKING else None

UPPER_ACTIVITY_LIMIT_MS = 60 * 1000 * 5 # 5 minutes
ONE_DAY_MS = 24 * 60 * 60 * 1000

@dataclass
class UserActivity:
    db: ClassVar[Database] = fake_db
    log: ClassVar[TraceLogger] = logging.getLogger("mau.user_activity")

    puppet_id: TelegramID
    first_activity_ts: int | None
    last_activity_ts: int | None

    columns: ClassVar[str] = "puppet_id, first_activity_ts, last_activity_ts"

    @classmethod
    def _from_row(cls, row: Record | None) -> Portal | None:
        if row is None:
            return None
        data = {**row}
        return cls(**data)

    @classmethod
    async def get_by_puppet_id(cls, tgid: TelegramID) -> Portal | None:
        q = f"SELECT {cls.columns} FROM user_activity WHERE tgid=$1"
        return cls._from_row(await cls.db.fetchrow(q, tgid, tg_receiver))

    @classmethod
    def update_for_puppet(cls, puppet: 'Puppet', activity_dt: datetime) -> None:
        activity_ts = int(activity_dt.timestamp() * 1000)

        if (time.time() * 1000) - activity_ts > UPPER_ACTIVITY_LIMIT_MS:
            return

        cls.log.debug(f"Updating activity time for {puppet.id} to {activity_ts}")
        obj = cls.get_by_puppet_id(puppet.id)
        if obj:
            obj.update(activity_ts)
        else:
            obj = UserActivity(
                puppet_id=puppet.id,
                first_activity_ts=activity_ts,
                last_activity_ts=activity_ts,
            )
            obj.insert()

    @classmethod
    def get_active_count(cls, min_activity_days: int, max_activity_days: int | None) -> int:
        current_ms = time.time() * 1000

        query = "SELECT COUNT(*) FROM user_activity WHERE (last_activity_ts - first_activity_ts) > $2"
        if max_activity_days is not None:
            query += " AND ($1 - last_activity_ts) <= $3"
        return cls.db.fetchval(query, current_ms, ONE_DAY_MS * min_activity_days, max_activity_days * ONE_DAY_MS)

    async def update(self, activity_ts: int) -> None:
        if self.last_activity_ts > activity_ts:
            return

        self.last_activity_ts = activity_ts

        await self.db.execute("UPDATE user_activity SET last_activity_ts = $2 WHERE puppet_id=$1", self.puppet_id, self.last_activity_ts)

    async def insert(self) -> None:
        await self.db.execute(
            f"INSERT INTO user_activity ({cls.columns}) VALUES ($1, $2, $3)",
            self.puppet_id,
            self.first_activity_ts,
            self.last_activity_ts
        )