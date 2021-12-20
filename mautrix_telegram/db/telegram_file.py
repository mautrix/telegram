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

from attr import dataclass

from mautrix.types import ContentURI, EncryptedFile
from mautrix.util.async_db import Database

fake_db = Database.create("") if TYPE_CHECKING else None


@dataclass
class TelegramFile:
    db: ClassVar[Database] = fake_db

    id: str
    mxc: ContentURI
    mime_type: str
    was_converted: bool
    timestamp: int
    size: int | None
    width: int | None
    height: int | None
    decryption_info: EncryptedFile | None
    thumbnail: TelegramFile | None = None

    @classmethod
    async def get(cls, loc_id: str, *, _thumbnail: bool = False) -> TelegramFile | None:
        q = (
            "SELECT id, mxc, mime_type, was_converted, timestamp, size, width, height, thumbnail,"
            "       decryption_info "
            "FROM telegram_file WHERE id=$1"
        )
        row = await cls.db.fetchrow(q, loc_id)
        if row is None:
            return None
        data = {**row}
        thumbnail_id = data.pop("thumbnail", None)
        if _thumbnail:
            # Don't allow more than one level of recursion
            thumbnail_id = None
        decryption_info = data.pop("decryption_info", None)
        return cls(
            **data,
            thumbnail=(await cls.get(thumbnail_id, _thumbnail=True)) if thumbnail_id else None,
            decryption_info=EncryptedFile.parse_json(decryption_info) if decryption_info else None,
        )

    async def insert(self) -> None:
        q = (
            "INSERT INTO telegram_file (id, mxc, mime_type, was_converted, size, width, height, "
            "                           thumbnail, decryption_info) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)"
        )
        await self.db.execute(
            q,
            self.id,
            self.mxc,
            self.mime_type,
            self.was_converted,
            self.size,
            self.width,
            self.height,
            self.thumbnail.id if self.thumbnail else None,
            self.decryption_info.json() if self.decryption_info else None,
        )
