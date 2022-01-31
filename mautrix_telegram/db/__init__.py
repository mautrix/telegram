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
from mautrix.util.async_db import Database

from .bot_chat import BotChat
from .disappearing_message import DisappearingMessage
from .message import Message
from .portal import Portal
from .puppet import Puppet
from .reaction import Reaction
from .telegram_file import TelegramFile
from .telethon_session import PgSession
from .upgrade import upgrade_table
from .user import User


def init(db: Database) -> None:
    for table in (
        Portal,
        Message,
        Reaction,
        User,
        Puppet,
        TelegramFile,
        BotChat,
        PgSession,
        DisappearingMessage,
    ):
        table.db = db


__all__ = [
    "upgrade_table",
    "init",
    "Portal",
    "Message",
    "Reaction",
    "User",
    "Puppet",
    "TelegramFile",
    "BotChat",
    "PgSession",
    "DisappearingMessage",
]
