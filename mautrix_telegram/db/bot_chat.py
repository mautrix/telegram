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

from mautrix.util.async_db import Database

from ..types import TelegramID

fake_db = Database.create("") if TYPE_CHECKING else None


# Fucking Telegram not telling bots what chats they are in 3:<
@dataclass
class BotChat:
    db: ClassVar[Database] = fake_db

    id: TelegramID
    type: str

    @classmethod
    def _from_row(cls, row: Record | None) -> BotChat | None:
        if row is None:
            return None
        return cls(**row)

    @classmethod
    async def delete_by_id(cls, chat_id: TelegramID) -> None:
        await cls.db.execute("DELETE FROM bot_chat WHERE id=$1", chat_id)

    @classmethod
    async def all(cls) -> list[BotChat]:
        rows = await cls.db.fetch("SELECT id, type FROM bot_chat")
        return [cls._from_row(row) for row in rows]

    async def insert(self) -> None:
        q = "INSERT INTO bot_chat (id, type) VALUES ($1, $2)"
        await self.db.execute(q, self.id, self.type)
