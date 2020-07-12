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
from sqlalchemy.engine.base import Engine

from mautrix.client.state_store.sqlalchemy import UserProfile, RoomState

from .bot_chat import BotChat
from .message import Message
from .portal import Portal
from .puppet import Puppet
from .telegram_file import TelegramFile
from .user import User, UserPortal, Contact


def init(db_engine: Engine) -> None:
    for table in (Portal, Message, User, Contact, UserPortal, Puppet, TelegramFile, UserProfile,
                  RoomState, BotChat):
        table.bind(db_engine)
