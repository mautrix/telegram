# -*- coding: future_fstrings -*-
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
from sqlalchemy import Column, ForeignKey, Integer, BigInteger, String, Boolean
from typing import Optional

from .base import Base


class TelegramFile(Base):
    __tablename__ = "telegram_file"

    id = Column(String, primary_key=True)
    mxc = Column(String)
    mime_type = Column(String)
    was_converted = Column(Boolean)
    timestamp = Column(BigInteger)
    size = Column(Integer, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    thumbnail_id = Column("thumbnail", String, ForeignKey("telegram_file.id"), nullable=True)
    thumbnail = None  # type: Optional[TelegramFile]

    @classmethod
    def get(cls, loc_id: str) -> Optional['TelegramFile']:
        rows = cls.db.execute(cls.t.select().where(cls.c.id == loc_id))
        try:
            loc_id, mxc, mime, conv, ts, s, w, h, thumb_id = next(rows)
            thumb = None
            if thumb_id:
                thumb = cls.get(thumb_id)
            return cls(id=loc_id, mxc=mxc, mime_type=mime, was_converted=conv, timestamp=ts,
                       size=s, width=w, height=h, thumbnail_id=thumb_id, thumbnail=thumb)
        except StopIteration:
            return None

    def insert(self) -> None:
        with self.db.begin() as conn:
            conn.execute(self.t.insert().values(
                id=self.id, mxc=self.mxc, mime_type=self.mime_type,
                was_converted=self.was_converted, timestamp=self.timestamp, size=self.size,
                width=self.width, height=self.height,
                thumbnail=self.thumbnail.id if self.thumbnail else self.thumbnail_id))
