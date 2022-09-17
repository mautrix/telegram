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

from mautrix.types import EventID, RoomID, UserID
from mautrix.util.async_db import Database, Scheme

from ..types import TelegramID

fake_db = Database.create("") if TYPE_CHECKING else None


@dataclass
class Message:
    db: ClassVar[Database] = fake_db

    mxid: EventID
    mx_room: RoomID
    tgid: TelegramID
    tg_space: TelegramID
    edit_index: int
    redacted: bool = False
    content_hash: bytes | None = None
    sender_mxid: UserID | None = None
    sender: TelegramID | None = None

    @classmethod
    def _from_row(cls, row: Record | None) -> Message | None:
        if row is None:
            return None
        return cls(**row)

    columns: ClassVar[str] = ", ".join(
        (
            "mxid",
            "mx_room",
            "tgid",
            "tg_space",
            "edit_index",
            "redacted",
            "content_hash",
            "sender_mxid",
            "sender",
        )
    )

    @classmethod
    async def get_all_by_tgid(cls, tgid: TelegramID, tg_space: TelegramID) -> list[Message]:
        q = f"SELECT {cls.columns} FROM message WHERE tgid=$1 AND tg_space=$2"
        rows = await cls.db.fetch(q, tgid, tg_space)
        return [cls._from_row(row) for row in rows]

    @classmethod
    async def get_one_by_tgid(
        cls, tgid: TelegramID, tg_space: TelegramID, edit_index: int = 0
    ) -> Message | None:
        if edit_index < 0:
            q = (
                f"SELECT {cls.columns} FROM message WHERE tgid=$1 AND tg_space=$2 "
                f"ORDER BY edit_index DESC LIMIT 1 OFFSET {-edit_index - 1}"
            )
            row = await cls.db.fetchrow(q, tgid, tg_space)
        else:
            q = (
                f"SELECT {cls.columns} FROM message"
                " WHERE tgid=$1 AND tg_space=$2 AND edit_index=$3"
            )
            row = await cls.db.fetchrow(q, tgid, tg_space, edit_index)
        return cls._from_row(row)

    @classmethod
    async def get_first_by_tgids(
        cls, tgids: list[TelegramID], tg_space: TelegramID
    ) -> list[Message]:
        if cls.db.scheme in (Scheme.POSTGRES, Scheme.COCKROACH):
            q = (
                f"SELECT {cls.columns} FROM message"
                " WHERE tgid=ANY($1) AND tg_space=$2 AND edit_index=0"
            )
            rows = await cls.db.fetch(q, tgids, tg_space)
        else:
            tgid_placeholders = ("?," * len(tgids)).rstrip(",")
            q = (
                f"SELECT {cls.columns} FROM message "
                f"WHERE tg_space=? AND edit_index=0 AND tgid IN ({tgid_placeholders})"
            )
            rows = await cls.db.fetch(q, tg_space, *tgids)
        return [cls._from_row(row) for row in rows]

    @classmethod
    async def count_spaces_by_mxid(cls, mxid: EventID, mx_room: RoomID) -> int:
        return (
            await cls.db.fetchval(
                "SELECT COUNT(tg_space) FROM message WHERE mxid=$1 AND mx_room=$2", mxid, mx_room
            )
            or 0
        )

    @classmethod
    async def find_last(cls, mx_room: RoomID, tg_space: TelegramID) -> Message | None:
        q = (
            f"SELECT {cls.columns} FROM message WHERE mx_room=$1 AND tg_space=$2 "
            f"ORDER BY tgid DESC LIMIT 1"
        )
        return cls._from_row(await cls.db.fetchrow(q, mx_room, tg_space))

    @classmethod
    async def delete_all(cls, mx_room: RoomID) -> None:
        await cls.db.execute("DELETE FROM message WHERE mx_room=$1", mx_room)

    @classmethod
    async def get_by_mxid(
        cls, mxid: EventID, mx_room: RoomID, tg_space: TelegramID
    ) -> Message | None:
        q = f"SELECT {cls.columns} FROM message WHERE mxid=$1 AND mx_room=$2 AND tg_space=$3"
        return cls._from_row(await cls.db.fetchrow(q, mxid, mx_room, tg_space))

    @classmethod
    async def get_by_mxids(
        cls, mxids: list[EventID], mx_room: RoomID, tg_space: TelegramID
    ) -> list[Message]:
        if cls.db.scheme in (Scheme.POSTGRES, Scheme.COCKROACH):
            q = (
                f"SELECT {cls.columns} FROM message"
                " WHERE mxid=ANY($1) AND mx_room=$2 AND tg_space=$3"
            )
            rows = await cls.db.fetch(q, mxids, mx_room, tg_space)
        else:
            mxid_placeholders = ("?," * len(mxids)).rstrip(",")
            q = (
                f"SELECT {cls.columns} FROM message "
                f"WHERE mx_room=? AND tg_space=? AND mxid IN ({mxid_placeholders})"
            )
            rows = await cls.db.fetch(q, mx_room, tg_space, *mxids)
        return [cls._from_row(row) for row in rows]

    @classmethod
    async def find_recent(
        cls, mx_room: RoomID, not_sender: TelegramID, limit: int = 20
    ) -> list[Message]:
        q = f"""
        SELECT {cls.columns} FROM message
        WHERE mx_room=$1 AND sender<>$2
        ORDER BY tgid DESC LIMIT $3
        """
        return [cls._from_row(row) for row in await cls.db.fetch(q, mx_room, not_sender, limit)]

    @classmethod
    async def replace_temp_mxid(cls, temp_mxid: str, mx_room: RoomID, real_mxid: EventID) -> None:
        q = "UPDATE message SET mxid=$1 WHERE mxid=$2 AND mx_room=$3"
        await cls.db.execute(q, real_mxid, temp_mxid, mx_room)

    @classmethod
    async def delete_temp_mxid(cls, temp_mxid: str, mx_room: RoomID) -> None:
        q = "DELETE FROM message WHERE mxid=$1 AND mx_room=$2"
        await cls.db.execute(q, temp_mxid, mx_room)

    @property
    def _values(self):
        return (
            self.mxid,
            self.mx_room,
            self.tgid,
            self.tg_space,
            self.edit_index,
            self.redacted,
            self.content_hash,
            self.sender_mxid,
            self.sender,
        )

    async def insert(self) -> None:
        q = """
            INSERT INTO message (
                mxid, mx_room, tgid, tg_space, edit_index, redacted, content_hash,
                sender_mxid, sender
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """
        await self.db.execute(q, *self._values)

    async def delete(self) -> None:
        q = "DELETE FROM message WHERE mxid=$1 AND mx_room=$2 AND tg_space=$3"
        await self.db.execute(q, self.mxid, self.mx_room, self.tg_space)

    async def mark_redacted(self) -> None:
        self.redacted = True
        q = "UPDATE message SET redacted=true WHERE mxid=$1 AND mx_room=$2"
        await self.db.execute(q, self.mxid, self.mx_room)
