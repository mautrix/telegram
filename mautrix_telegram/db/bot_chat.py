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

from mautrix.util.db import Base

from ..types import TelegramID


# Fucking Telegram not telling bots what chats they are in 3:<
class BotChat(Base):
    __tablename__ = "bot_chat"
    id: TelegramID = Column(Integer, primary_key=True)
    type: str = Column(String, nullable=False)

    @classmethod
    def delete_by_id(cls, chat_id: TelegramID) -> None:
        with cls.db.begin() as conn:
            conn.execute(cls.t.delete().where(cls.c.id == chat_id))

    @classmethod
    def all(cls) -> Iterable['BotChat']:
        return cls._select_all()
