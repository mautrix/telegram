# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2019 Tulir Asokan
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
from typing import Optional

from sqlalchemy import Column, ForeignKey, Integer, BigInteger, String, Boolean
from sqlalchemy.engine.result import RowProxy

from mautrix.types import ContentURI
from mautrix.util.db import Base


class TelegramFile(Base):
    __tablename__ = "telegram_file"

    id: str = Column(String, primary_key=True)
    mxc: ContentURI = Column(String)
    mime_type: str = Column(String)
    was_converted: bool = Column(Boolean)
    timestamp: int = Column(BigInteger)
    size: Optional[int] = Column(Integer, nullable=True)
    width: Optional[int] = Column(Integer, nullable=True)
    height: Optional[int] = Column(Integer, nullable=True)
    thumbnail_id: str = Column("thumbnail", String, ForeignKey("telegram_file.id"), nullable=True)
    thumbnail: Optional['TelegramFile'] = None

    @classmethod
    def scan(cls, row: RowProxy) -> 'TelegramFile':
        telegram_file: TelegramFile = super().scan(row)
        if isinstance(telegram_file.thumbnail, str):
            telegram_file.thumbnail = cls.get(telegram_file.thumbnail)
        return telegram_file

    @classmethod
    def get(cls, loc_id: str) -> Optional['TelegramFile']:
        return cls._select_one_or_none(cls.c.id == loc_id)

    def insert(self) -> None:
        with self.db.begin() as conn:
            conn.execute(self.t.insert().values(
                id=self.id, mxc=self.mxc, mime_type=self.mime_type,
                was_converted=self.was_converted, timestamp=self.timestamp, size=self.size,
                width=self.width, height=self.height,
                thumbnail=self.thumbnail.id if self.thumbnail else self.thumbnail_id))
