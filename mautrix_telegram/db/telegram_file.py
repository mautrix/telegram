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

from mautrix.types import ContentURI, EncryptedFile
from mautrix.util.async_db import Database, Scheme

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

    columns: ClassVar[str] = (
        "id, mxc, mime_type, was_converted, timestamp, size, width, height, thumbnail, "
        "decryption_info"
    )

    @classmethod
    def _from_row(cls, row: Record | None) -> TelegramFile | None:
        if row is None:
            return None
        data = {**row}
        data.pop("thumbnail", None)
        decryption_info = data.pop("decryption_info", None)
        return cls(
            **data,
            thumbnail=None,
            decryption_info=EncryptedFile.parse_json(decryption_info) if decryption_info else None,
        )

    @classmethod
    async def get_many(cls, loc_ids: list[str]) -> list[TelegramFile]:
        if cls.db.scheme in (Scheme.POSTGRES, Scheme.COCKROACH):
            q = f"SELECT {cls.columns} FROM telegram_file WHERE id=ANY($1)"
            rows = await cls.db.fetch(q, loc_ids)
        else:
            tgid_placeholders = ("?," * len(loc_ids)).rstrip(",")
            q = f"SELECT {cls.columns} FROM telegram_file WHERE id IN ({tgid_placeholders})"
            rows = await cls.db.fetch(q, *loc_ids)
        return [cls._from_row(row) for row in rows]

    @classmethod
    async def get(cls, loc_id: str, *, _thumbnail: bool = False) -> TelegramFile | None:
        q = f"SELECT {cls.columns} FROM telegram_file WHERE id=$1"
        row = await cls.db.fetchrow(q, loc_id)
        file = cls._from_row(row)
        if file is None:
            return None
        try:
            thumbnail_id = row["thumbnail"]
        except KeyError:
            thumbnail_id = None
        if thumbnail_id and not _thumbnail:
            file.thumbnail = await cls.get(thumbnail_id, _thumbnail=True)
        return file

    @classmethod
    async def find_by_mxc(cls, mxc: ContentURI) -> TelegramFile | None:
        q = f"SELECT {cls.columns} FROM telegram_file WHERE mxc=$1"
        return cls._from_row(await cls.db.fetchrow(q, mxc))

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
