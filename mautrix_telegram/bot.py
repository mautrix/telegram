# -*- coding: future_fstrings -*-
# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2018 Tulir Asokan
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
import logging

from telethon.tl.types import *

from .abstract_user import AbstractUser
from .db import BotChat

config = None


class Bot(AbstractUser):
    log = logging.getLogger("mau.bot")

    def __init__(self, token):
        super().__init__()
        self.token = token
        self.whitelisted = True
        self._init_client()
        self.chats = {chat.id for chat in BotChat.query.all()}

    async def start(self):
        await super().start()
        if not self.logged_in:
            await self.client.sign_in(bot_token=self.token)
        await self.post_login()
        return self

    async def post_login(self):
        info = await self.client.get_me()
        self.tgid = info.id

    async def update(self, update):
        if not isinstance(update, (UpdateNewMessage, UpdateNewChannelMessage)):
            return
        elif not isinstance(update.message, MessageService):
            return
        action = update.message.action
        to_id = update.message.to_id
        to_id = to_id.chat_id if isinstance(to_id, PeerChat) else to_id.channel_id
        if isinstance(action, MessageActionChatAddUser):
            if self.tgid in action.users:
                self.chats.add(to_id)
                self.db.add(BotChat(id=to_id))
                self.db.commit()
        elif isinstance(action, MessageActionChatDeleteUser):
            if action.user_id == self.tgid:
                self.chats.remove(to_id)
                BotChat.query.get(to_id).delete()
                self.db.commit()

    def is_in_chat(self, peer_id):
        return peer_id in self.chats

    @property
    def name(self):
        return "bot"


def init(context):
    global config
    config = context.config
    token = config["telegram.bot_token"]
    if token:
        return Bot(token)
    return None
