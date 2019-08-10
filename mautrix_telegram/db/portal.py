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

from sqlalchemy import Column, Integer, String, Boolean, Text, and_
from sqlalchemy.engine.result import RowProxy
from sqlalchemy.sql.expression import ClauseElement

from mautrix.types import RoomID
from mautrix.bridge.db import Base

from ..types import TelegramID


class Portal(Base):
    __tablename__ = "portal"

    # Telegram chat information
    tgid: TelegramID = Column(Integer, primary_key=True)
    tg_receiver: TelegramID = Column(Integer, primary_key=True)
    peer_type: str = Column(String, nullable=False)
    megagroup: bool = Column(Boolean)

    # Matrix portal information
    mxid: RoomID = Column(String, unique=True, nullable=True)

    config: str = Column(Text, nullable=True)

    # Telegram chat metadata
    username: str = Column(String, nullable=True)
    title: str = Column(String, nullable=True)
    about: str = Column(String, nullable=True)
    photo_id: str = Column(String, nullable=True)

    @classmethod
    def scan(cls, row: RowProxy) -> Optional['Portal']:
        (tgid, tg_receiver, peer_type, megagroup, mxid, config, username, title, about,
         photo_id) = row
        return cls(tgid=tgid, tg_receiver=tg_receiver, peer_type=peer_type, megagroup=megagroup,
                   mxid=mxid, config=config, username=username, title=title, about=about,
                   photo_id=photo_id)

    @classmethod
    def get_by_tgid(cls, tgid: TelegramID, tg_receiver: TelegramID) -> Optional['Portal']:
        return cls._select_one_or_none(and_(cls.c.tgid == tgid, cls.c.tg_receiver == tg_receiver))

    @classmethod
    def get_by_mxid(cls, mxid: RoomID) -> Optional['Portal']:
        return cls._select_one_or_none(cls.c.mxid == mxid)

    @classmethod
    def get_by_username(cls, username: str) -> Optional['Portal']:
        return cls._select_one_or_none(cls.c.username == username)

    @property
    def _edit_identity(self) -> ClauseElement:
        return and_(self.c.tgid == self.tgid, self.c.tg_receiver == self.tg_receiver)

    def insert(self) -> None:
        with self.db.begin() as conn:
            conn.execute(self.t.insert().values(
                tgid=self.tgid, tg_receiver=self.tg_receiver, peer_type=self.peer_type,
                megagroup=self.megagroup, mxid=self.mxid, config=self.config,
                username=self.username, title=self.title, about=self.about, photo_id=self.photo_id))
