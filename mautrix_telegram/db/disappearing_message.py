# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2021 Sumner Evans
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

import asyncpg

from mautrix.bridge import AbstractDisappearingMessage
from mautrix.types import EventID, RoomID
from mautrix.util.async_db import Database

fake_db = Database.create("") if TYPE_CHECKING else None


class DisappearingMessage(AbstractDisappearingMessage):
    db: ClassVar[Database] = fake_db

    async def insert(self) -> None:
        q = """
            INSERT INTO disappearing_message (room_id, event_id, expiration_seconds, expiration_ts)
            VALUES ($1, $2, $3, $4)
        """
        await self.db.execute(
            q, self.room_id, self.event_id, self.expiration_seconds, self.expiration_ts
        )

    async def update(self) -> None:
        q = "UPDATE disappearing_message SET expiration_ts=$3 WHERE room_id=$1 AND event_id=$2"
        await self.db.execute(q, self.room_id, self.event_id, self.expiration_ts)

    async def delete(self) -> None:
        q = "DELETE from disappearing_message WHERE room_id=$1 AND event_id=$2"
        await self.db.execute(q, self.room_id, self.event_id)

    @classmethod
    def _from_row(cls, row: asyncpg.Record) -> DisappearingMessage:
        return cls(**row)

    @classmethod
    async def get(cls, room_id: RoomID, event_id: EventID) -> DisappearingMessage | None:
        q = """
            SELECT room_id, event_id, expiration_seconds, expiration_ts FROM disappearing_message
            WHERE room_id=$1 AND mxid=$2
        """
        try:
            return cls._from_row(await cls.db.fetchrow(q, room_id, event_id))
        except Exception:
            return None

    @classmethod
    async def get_all_scheduled(cls) -> list[DisappearingMessage]:
        q = """
            SELECT room_id, event_id, expiration_seconds, expiration_ts FROM disappearing_message
            WHERE expiration_ts IS NOT NULL
        """
        return [cls._from_row(r) for r in await cls.db.fetch(q)]

    @classmethod
    async def get_unscheduled_for_room(cls, room_id: RoomID) -> list[DisappearingMessage]:
        q = """
            SELECT room_id, event_id, expiration_seconds, expiration_ts FROM disappearing_message
            WHERE room_id = $1 AND expiration_ts IS NULL
        """
        return [cls._from_row(r) for r in await cls.db.fetch(q, room_id)]
