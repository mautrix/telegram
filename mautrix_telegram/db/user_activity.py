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
from typing import Optional, Iterable

from sqlalchemy import Column, Integer, BigInteger
from sqlalchemy.ext.hybrid import hybrid_property

from mautrix.util.db import Base
from mautrix.util.logging import TraceLogger

from ..types import TelegramID

import logging
import datetime
import time

UPPER_ACTIVITY_LIMIT_MS = 60 * 1000 * 5 # 5 minutes
ONE_DAY_MS = 24 * 60 * 60 * 1000


class UserActivity(Base):
    __tablename__ = "user_activity"

    log: TraceLogger = logging.getLogger("mau.user_activity")

    puppet_id: TelegramID = Column(BigInteger, primary_key=True)
    first_activity_ts: Optional[int] = Column(BigInteger)
    last_activity_ts: Optional[int] = Column(BigInteger)

    def update(self, activity_ts: int) -> None:
        if self.last_activity_ts > activity_ts:
            return

        self.last_activity_ts = activity_ts

        self.edit(last_activity_ts=self.last_activity_ts)

    @classmethod
    def update_for_puppet(cls, puppet: 'Puppet', activity_dt: datetime) -> None:
        activity_ts = int(activity_dt.timestamp() * 1000)

        if (time.time() * 1000) - activity_ts > UPPER_ACTIVITY_LIMIT_MS:
            return

        cls.log.debug(f"Updating activity time for {puppet.id} to {activity_ts}")
        obj = cls._select_one_or_none(cls.c.puppet_id == puppet.id)
        if obj:
            obj.update(activity_ts)
        else:
            obj = UserActivity(
                puppet_id=puppet.id,
                first_activity_ts=activity_ts,
                last_activity_ts=activity_ts,
            )
            obj.insert()

    @hybrid_property
    def activity_days(self):
        return (self.last_activity_ts - self.first_activity_ts / 1000) / ONE_DAY_MS

    @classmethod
    def get_active_count(cls, min_activity_days: int, max_activity_days: Optional[int]) -> int:
        current_ms = time.time() * 1000

        query = cls.t.select().where(cls.activity_days > min_activity_days)
        if max_activity_days is not None:
            query = query.where((current_ms - cls.last_activity_ts) <= (max_activity_days * ONE_DAY_MS))
        return cls.db.execute(query.count()).scalar()
