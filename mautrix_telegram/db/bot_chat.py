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
from typing import Iterable

from sqlalchemy import Column, Integer, String

from ..types import TelegramID
from .base import Base


# Fucking Telegram not telling bots what chats they are in 3:<
class BotChat(Base):
    __tablename__ = "bot_chat"
    id = Column(Integer, primary_key=True)  # type: TelegramID
    type = Column(String, nullable=False)

    @classmethod
    def delete(cls, chat_id: TelegramID) -> None:
        with cls.db.begin() as conn:
            conn.execute(cls.t.delete().where(cls.c.id == chat_id))

    @classmethod
    def all(cls) -> Iterable['BotChat']:
        rows = cls.db.execute(cls.t.select())
        for row in rows:
            chat_id, chat_type = row
            yield cls(id=chat_id, type=chat_type)

    def insert(self) -> None:
        with self.db.begin() as conn:
            conn.execute(self.t.insert().values(id=self.id, type=self.type))
