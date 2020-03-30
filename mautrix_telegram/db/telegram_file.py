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
from typing import Optional, cast, Dict, Any

from sqlalchemy import (Column, ForeignKey, Integer, BigInteger, String, Boolean, Text,
                        TypeDecorator)
from sqlalchemy.engine.result import RowProxy

from mautrix.types import ContentURI, EncryptedFile
from mautrix.util.db import Base


class DBEncryptedFile(TypeDecorator):
    impl = Text

    @property
    def python_type(self):
        return EncryptedFile

    def process_bind_param(self, value: EncryptedFile, dialect) -> Optional[str]:
        if value is not None:
            return value.json()
        return None

    def process_result_value(self, value: str, dialect) -> Optional[EncryptedFile]:
        if value is not None:
            return EncryptedFile.parse_json(value)
        return None

    def process_literal_param(self, value, dialect):
        return value


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
    decryption_info: Optional[Dict[str, Any]] = Column(DBEncryptedFile, nullable=True)
    thumbnail_id: str = Column("thumbnail", String, ForeignKey("telegram_file.id"), nullable=True)
    thumbnail: Optional['TelegramFile'] = None

    @classmethod
    def scan(cls, row: RowProxy) -> 'TelegramFile':
        telegram_file = cast(TelegramFile, super().scan(row))
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
                width=self.width, height=self.height, decryption_info=self.decryption_info,
                thumbnail=self.thumbnail.id if self.thumbnail else self.thumbnail_id))
