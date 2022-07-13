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

from typing import TYPE_CHECKING, Any, ClassVar
import json

from asyncpg import Record
from attr import dataclass
import attr

from mautrix.types import BatchID, ContentURI, EventID, RoomID
from mautrix.util.async_db import Database

from ..types import TelegramID

fake_db = Database.create("") if TYPE_CHECKING else None


@dataclass
class Portal:
    db: ClassVar[Database] = fake_db

    # Telegram chat information
    tgid: TelegramID
    tg_receiver: TelegramID
    peer_type: str
    megagroup: bool

    # Matrix portal information
    mxid: RoomID | None
    avatar_url: ContentURI | None
    encrypted: bool
    first_event_id: EventID | None
    next_batch_id: BatchID | None
    base_insertion_id: EventID | None

    sponsored_event_id: EventID | None
    sponsored_event_ts: int | None
    sponsored_msg_random_id: bytes | None

    # Telegram chat metadata
    username: str | None
    title: str | None
    about: str | None
    photo_id: str | None
    name_set: bool
    avatar_set: bool

    local_config: dict[str, Any] = attr.ib(factory=lambda: {})

    @classmethod
    def _from_row(cls, row: Record | None) -> Portal | None:
        if row is None:
            return None
        data = {**row}
        data["local_config"] = json.loads(data.pop("config", None) or "{}")
        return cls(**data)

    columns: ClassVar[str] = ", ".join(
        (
            "tgid",
            "tg_receiver",
            "peer_type",
            "megagroup",
            "mxid",
            "avatar_url",
            "encrypted",
            "first_event_id",
            "next_batch_id",
            "base_insertion_id",
            "sponsored_event_id",
            "sponsored_event_ts",
            "sponsored_msg_random_id",
            "username",
            "title",
            "about",
            "photo_id",
            "name_set",
            "avatar_set",
            "config",
        )
    )

    @classmethod
    async def get_by_tgid(cls, tgid: TelegramID, tg_receiver: TelegramID) -> Portal | None:
        q = f"SELECT {cls.columns} FROM portal WHERE tgid=$1 AND tg_receiver=$2"
        return cls._from_row(await cls.db.fetchrow(q, tgid, tg_receiver))

    @classmethod
    async def get_by_mxid(cls, mxid: RoomID) -> Portal | None:
        q = f"SELECT {cls.columns} FROM portal WHERE mxid=$1"
        return cls._from_row(await cls.db.fetchrow(q, mxid))

    @classmethod
    async def find_by_username(cls, username: str) -> Portal | None:
        q = f"SELECT {cls.columns} FROM portal WHERE lower(username)=$1"
        return cls._from_row(await cls.db.fetchrow(q, username.lower()))

    @classmethod
    async def find_private_chats_of(cls, tg_receiver: TelegramID) -> list[Portal]:
        q = f"SELECT {cls.columns} FROM portal WHERE tg_receiver=$1 AND peer_type='user'"
        return [cls._from_row(row) for row in await cls.db.fetch(q, tg_receiver)]

    @classmethod
    async def find_private_chats_with(cls, tgid: TelegramID) -> list[Portal]:
        q = f"SELECT {cls.columns} FROM portal WHERE tgid=$1 AND peer_type='user'"
        return [cls._from_row(row) for row in await cls.db.fetch(q, tgid)]

    @classmethod
    async def all(cls) -> list[Portal]:
        rows = await cls.db.fetch(f"SELECT {cls.columns} FROM portal")
        return [cls._from_row(row) for row in rows]

    @property
    def _values(self):
        return (
            self.tgid,
            self.tg_receiver,
            self.peer_type,
            self.mxid,
            self.avatar_url,
            self.encrypted,
            self.first_event_id,
            self.next_batch_id,
            self.base_insertion_id,
            self.sponsored_event_id,
            self.sponsored_event_ts,
            self.sponsored_msg_random_id,
            self.username,
            self.title,
            self.about,
            self.photo_id,
            self.name_set,
            self.avatar_set,
            self.megagroup,
            json.dumps(self.local_config) if self.local_config else None,
        )

    async def save(self) -> None:
        q = """
        UPDATE portal
        SET mxid=$4, avatar_url=$5, encrypted=$6,
            first_event_id=$7, next_batch_id=$8, base_insertion_id=$9,
            sponsored_event_id=$10, sponsored_event_ts=$11, sponsored_msg_random_id=$12,
            username=$13, title=$14, about=$15, photo_id=$16, name_set=$17, avatar_set=$18,
            megagroup=$19, config=$20
        WHERE tgid=$1 AND tg_receiver=$2 AND (peer_type=$3 OR true)
        """
        await self.db.execute(q, *self._values)

    async def update_id(self, id: TelegramID, peer_type: str) -> None:
        q = (
            "UPDATE portal SET tgid=$1, tg_receiver=$1, peer_type=$2 "
            "WHERE tgid=$3 AND tg_receiver=$3"
        )
        await self.db.execute(q, id, peer_type, self.tgid)
        self.tgid = id
        self.tg_receiver = id
        self.peer_type = peer_type

    async def insert(self) -> None:
        q = """
        INSERT INTO portal (
            tgid, tg_receiver, peer_type, mxid, avatar_url, encrypted,
            first_event_id, base_insertion_id, next_batch_id,
            sponsored_event_id, sponsored_event_ts, sponsored_msg_random_id,
            username, title, about, photo_id, name_set, avatar_set, megagroup, config
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18,
                  $19, $20)
        """
        await self.db.execute(q, *self._values)

    async def delete(self) -> None:
        q = "DELETE FROM portal WHERE tgid=$1 AND tg_receiver=$2"
        await self.db.execute(q, self.tgid, self.tg_receiver)
