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

from typing import TYPE_CHECKING, ClassVar, Iterable

from asyncpg import Record
from attr import dataclass

from mautrix.types import UserID
from mautrix.util.async_db import Database, Scheme

from ..types import TelegramID

fake_db = Database.create("") if TYPE_CHECKING else None


@dataclass
class User:
    db: ClassVar[Database] = fake_db

    mxid: UserID
    tgid: TelegramID | None
    tg_username: str | None
    tg_phone: str | None
    is_bot: bool
    is_premium: bool
    saved_contacts: int

    @classmethod
    def _from_row(cls, row: Record | None) -> User | None:
        if row is None:
            return None
        return cls(**row)

    columns: ClassVar[str] = ", ".join(
        ("mxid", "tgid", "tg_username", "tg_phone", "is_bot", "is_premium", "saved_contacts")
    )

    @classmethod
    async def get_by_tgid(cls, tgid: TelegramID) -> User | None:
        q = f'SELECT {cls.columns} FROM "user" WHERE tgid=$1'
        return cls._from_row(await cls.db.fetchrow(q, tgid))

    @classmethod
    async def get_by_mxid(cls, mxid: UserID) -> User | None:
        q = f'SELECT {cls.columns} FROM "user" WHERE mxid=$1'
        return cls._from_row(await cls.db.fetchrow(q, mxid))

    @classmethod
    async def find_by_username(cls, username: str) -> User | None:
        q = f'SELECT {cls.columns} FROM "user" WHERE lower(tg_username)=$1'
        return cls._from_row(await cls.db.fetchrow(q, username.lower()))

    @classmethod
    async def all_with_tgid(cls) -> list[User]:
        q = f'SELECT {cls.columns} FROM "user" WHERE tgid IS NOT NULL'
        return [cls._from_row(row) for row in await cls.db.fetch(q)]

    async def delete(self) -> None:
        await self.db.execute('DELETE FROM "user" WHERE mxid=$1', self.mxid)

    @property
    def _values(self):
        return (
            self.mxid,
            self.tgid,
            self.tg_username,
            self.tg_phone,
            self.is_bot,
            self.is_premium,
            self.saved_contacts,
        )

    async def save(self) -> None:
        q = """
        UPDATE "user" SET tgid=$2, tg_username=$3, tg_phone=$4, is_bot=$5, is_premium=$6,
                          saved_contacts=$7
        WHERE mxid=$1
        """
        await self.db.execute(q, *self._values)

    async def insert(self) -> None:
        q = """
        INSERT INTO "user" (mxid, tgid, tg_username, tg_phone, is_bot, is_premium, saved_contacts)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """
        await self.db.execute(q, *self._values)

    async def get_contacts(self) -> list[TelegramID]:
        rows = await self.db.fetch('SELECT contact FROM contact WHERE "user"=$1', self.tgid)
        return [TelegramID(row["contact"]) for row in rows]

    async def set_contacts(self, puppets: Iterable[TelegramID]) -> None:
        columns = ["user", "contact"]
        records = [(self.tgid, puppet_id) for puppet_id in puppets]
        async with self.db.acquire() as conn, conn.transaction():
            await conn.execute('DELETE FROM contact WHERE "user"=$1', self.tgid)
            if self.db.scheme == Scheme.POSTGRES:
                await conn.copy_records_to_table("contact", records=records, columns=columns)
            else:
                q = 'INSERT INTO contact ("user", contact) VALUES ($1, $2)'
                await conn.executemany(q, records)

    async def get_portals(self) -> list[tuple[TelegramID, TelegramID]]:
        q = 'SELECT portal, portal_receiver FROM user_portal WHERE "user"=$1'
        rows = await self.db.fetch(q, self.tgid)
        return [(TelegramID(row["portal"]), TelegramID(row["portal_receiver"])) for row in rows]

    async def set_portals(self, portals: Iterable[tuple[TelegramID, TelegramID]]) -> None:
        columns = ["user", "portal", "portal_receiver"]
        records = [(self.tgid, tgid, tg_receiver) for tgid, tg_receiver in portals]
        async with self.db.acquire() as conn, conn.transaction():
            await conn.execute('DELETE FROM user_portal WHERE "user"=$1', self.tgid)
            if self.db.scheme == Scheme.POSTGRES:
                await conn.copy_records_to_table("user_portal", records=records, columns=columns)
            else:
                q = 'INSERT INTO user_portal ("user", portal, portal_receiver) VALUES ($1, $2, $3)'
                await conn.executemany(q, records)

    async def register_portal(self, tgid: TelegramID, tg_receiver: TelegramID) -> None:
        q = (
            'INSERT INTO user_portal ("user", portal, portal_receiver) VALUES ($1, $2, $3) '
            'ON CONFLICT ("user", portal, portal_receiver) DO NOTHING'
        )
        await self.db.execute(q, self.tgid, tgid, tg_receiver)

    async def unregister_portal(self, tgid: TelegramID, tg_receiver: TelegramID) -> None:
        q = 'DELETE FROM user_portal WHERE "user"=$1 AND portal=$2 AND portal_receiver=$3'
        await self.db.execute(q, self.tgid, tgid, tg_receiver)
