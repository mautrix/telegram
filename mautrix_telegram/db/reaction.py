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

from typing import TYPE_CHECKING, ClassVar

from asyncpg import Record
from attr import dataclass
from telethon.tl.types import ReactionCustomEmoji, ReactionEmoji, TypeReaction

from mautrix.types import EventID, RoomID
from mautrix.util.async_db import Database

from ..types import TelegramID

fake_db = Database.create("") if TYPE_CHECKING else None


@dataclass
class Reaction:
    db: ClassVar[Database] = fake_db

    mxid: EventID
    mx_room: RoomID
    msg_mxid: EventID
    tg_sender: TelegramID
    reaction: str

    @classmethod
    def _from_row(cls, row: Record | None) -> Reaction | None:
        if row is None:
            return None
        return cls(**row)

    columns: ClassVar[str] = "mxid, mx_room, msg_mxid, tg_sender, reaction"

    @classmethod
    async def delete_all(cls, mx_room: RoomID) -> None:
        await cls.db.execute("DELETE FROM reaction WHERE mx_room=$1", mx_room)

    @classmethod
    async def get_by_mxid(cls, mxid: EventID, mx_room: RoomID) -> Reaction | None:
        q = f"SELECT {cls.columns} FROM reaction WHERE mxid=$1 AND mx_room=$2"
        return cls._from_row(await cls.db.fetchrow(q, mxid, mx_room))

    @classmethod
    async def get_by_sender(
        cls, mxid: EventID, mx_room: RoomID, tg_sender: TelegramID
    ) -> list[Reaction]:
        q = f"SELECT {cls.columns} FROM reaction WHERE msg_mxid=$1 AND mx_room=$2 AND tg_sender=$3"
        rows = await cls.db.fetch(q, mxid, mx_room, tg_sender)
        return [cls._from_row(row) for row in rows]

    @classmethod
    async def get_all_by_message(cls, mxid: EventID, mx_room: RoomID) -> list[Reaction]:
        q = f"SELECT {cls.columns} FROM reaction WHERE msg_mxid=$1 AND mx_room=$2"
        rows = await cls.db.fetch(q, mxid, mx_room)
        return [cls._from_row(row) for row in rows]

    @property
    def telegram(self) -> TypeReaction:
        if self.reaction.isdecimal():
            return ReactionCustomEmoji(document_id=int(self.reaction))
        else:
            return ReactionEmoji(emoticon=self.reaction)

    @property
    def _values(self):
        return (
            self.mxid,
            self.mx_room,
            self.msg_mxid,
            self.tg_sender,
            self.reaction,
        )

    async def save(self) -> None:
        q = """
            INSERT INTO reaction (mxid, mx_room, msg_mxid, tg_sender, reaction)
            VALUES ($1, $2, $3, $4, $5) ON CONFLICT (msg_mxid, mx_room, tg_sender, reaction)
                DO UPDATE SET mxid=excluded.mxid
        """
        await self.db.execute(q, *self._values)

    async def delete(self) -> None:
        q = "DELETE FROM reaction WHERE msg_mxid=$1 AND mx_room=$2 AND tg_sender=$3 AND reaction=$4"
        await self.db.execute(q, self.msg_mxid, self.mx_room, self.tg_sender, self.reaction)
