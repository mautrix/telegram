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
import time

from mautrix.bridge import AbstractDisappearingMessage
from mautrix.types import EventID, RoomID
from mautrix.util.async_db import Database

fake_db = Database.create("") if TYPE_CHECKING else None


class DisappearingMessage(AbstractDisappearingMessage):
    unqueued_ts: int | None = None
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

    """
    Get all scheduled messages that will expire in given seconds that haven't yet been unqueued.

    This will also stamp them in the database for being unqueued so every time this method is called
    there should be a unique set of events. If seconds is None then all events will be returned
    regardless of being requested before.

    The first call on startup should be with None and subsequent with the previous value.
    """
    @classmethod
    async def unqueue_expiring(cls, seconds: int | None = None) -> list[DisappearingMessage]:
        unqueued_ts = int(time.time() * 1000)

        rows = None
        if seconds is None:
            q = """
                SELECT room_id, event_id, expiration_seconds, expiration_ts FROM disappearing_message
                WHERE expiration_ts <= $1
            """
            rows = await cls.db.fetch(q, unqueued_ts)
        else:
            q = """
                SELECT room_id, event_id, expiration_seconds, expiration_ts FROM disappearing_message
                WHERE expiration_ts <= $1 AND (unqueued_ts IS NULL OR unqueued_ts < $2)
            """
            rows = await cls.db.fetch(q, unqueued_ts + (seconds * 1000), unqueued_ts)

        msgs = [cls._from_row(r) for r in rows]
        for msg in msgs:
            msg.unqueued_ts = unqueued_ts
            await msg.update()

        return msgs

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
        # Stubbed because we pump with unqueue_expiring
        return []

    @classmethod
    async def get_unscheduled_for_room(cls, room_id: RoomID) -> list[DisappearingMessage]:
        # Stubbed because we pump with unqueue_expiring
        return []
