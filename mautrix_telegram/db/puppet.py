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
from typing import Optional, Iterable

from sqlalchemy import Column, Integer, String, Text, Boolean
from sqlalchemy.sql import expression, func

from mautrix.types import UserID, SyncToken
from mautrix.util.db import Base

from ..types import TelegramID


class Puppet(Base):
    __tablename__ = "puppet"

    id: TelegramID = Column(Integer, primary_key=True)
    custom_mxid: UserID = Column(String, nullable=True)
    access_token: str = Column(String, nullable=True)
    next_batch: SyncToken = Column(String, nullable=True)
    base_url: str = Column(Text, nullable=True)
    displayname: str = Column(String, nullable=True)
    displayname_source: TelegramID = Column(Integer, nullable=True)
    username: str = Column(String, nullable=True)
    photo_id: str = Column(String, nullable=True)
    is_bot: bool = Column(Boolean, nullable=True)
    matrix_registered: bool = Column(Boolean, nullable=False, server_default=expression.false())
    disable_updates: bool = Column(Boolean, nullable=False, server_default=expression.false())

    @classmethod
    def all_with_custom_mxid(cls) -> Iterable['Puppet']:
        yield from cls._select_all(cls.c.custom_mxid != None)

    @classmethod
    def get_by_tgid(cls, tgid: TelegramID) -> Optional['Puppet']:
        return cls._select_one_or_none(cls.c.id == tgid)

    @classmethod
    def get_by_custom_mxid(cls, mxid: UserID) -> Optional['Puppet']:
        return cls._select_one_or_none(cls.c.custom_mxid == mxid)

    @classmethod
    def get_by_username(cls, username: str) -> Optional['Puppet']:
        return cls._select_one_or_none(func.lower(cls.c.username) == username)

    @classmethod
    def get_by_displayname(cls, displayname: str) -> Optional['Puppet']:
        return cls._select_one_or_none(cls.c.displayname == displayname)
