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
import re

from telethon.tl.types import *
from telethon.errors import ChannelInvalidError, ChannelPrivateError
from telethon.tl.functions.messages import GetChatsRequest
from telethon.tl.functions.channels import GetChannelsRequest

from .abstract_user import AbstractUser
from .db import BotChat
from . import puppet as pu, portal as po, user as u

config = None


class Bot(AbstractUser):
    log = logging.getLogger("mau.bot")
    mxid_regex = re.compile("@.+:.+")

    def __init__(self, token):
        super().__init__()
        self.token = token
        self.whitelisted = True
        self.username = None
        self._init_client()
        self.chats = {chat.id: chat.type for chat in BotChat.query.all()}

    async def start(self):
        await super().start()
        if not self.logged_in:
            await self.client.sign_in(bot_token=self.token)
        await self.post_login()
        return self

    async def post_login(self):
        info = await self.client.get_me()
        self.tgid = info.id
        self.username = info.username
        self.mxid = pu.Puppet.get_mxid_from_id(self.tgid)

        chat_ids = [id for id, type in self.chats.items() if type == "chat"]
        response = await self.client(GetChatsRequest(chat_ids))
        for chat in response.chats:
            if isinstance(chat, ChatForbidden) or chat.left or chat.deactivated:
                self.remove_chat(chat.id)

        channel_ids = [InputChannel(id, 0)
                       for id, type in self.chats.items()
                       if type == "channel"]
        for id in channel_ids:
            try:
                await self.client(GetChannelsRequest([id]))
            except (ChannelPrivateError, ChannelInvalidError):
                self.remove_chat(id.channel_id)

    def register_portal(self, portal):
        self.add_chat(portal.tgid, portal.peer_type)

    def unregister_portal(self, portal):
        self.remove_chat(portal.tgid)

    def add_chat(self, id, type):
        if id not in self.chats:
            self.chats[id] = type
            self.db.add(BotChat(id=id, type=type))
            self.db.commit()

    def remove_chat(self, id):
        try:
            del self.chats[id]
        except KeyError:
            pass
        self.db.delete(BotChat.query.get(id))
        self.db.commit()

    async def handle_command_portal(self, portal, reply):
        if not config["bridge.authless_relaybot_portals"]:
            return await reply("This bridge doesn't allow portal creation/invites from Telegram.")

        await portal.create_matrix_room(self)
        if portal.mxid:
            if portal.username:
                return await reply(
                    f"Portal is public: [{portal.alias}](https://matrix.to/#/{portal.alias})")
            else:
                return await reply(
                    "Portal is not public. Use `/invite <mxid>` to get an invite.")

    async def handle_command_invite(self, portal, reply, mxid):
        if len(mxid) == 0:
            return await reply("Usage: `/invite <mxid>`")
        elif not portal.mxid:
            return await reply("Portal does not have Matrix room. "
                               "Create one with /portal first.")
        if not self.mxid_regex.match(mxid):
            return await reply("That doesn't look like a Matrix ID.")
        user = await u.User.get_by_mxid(mxid).ensure_started()
        if not user.whitelisted:
            return await reply("That user is not whitelisted to use the bridge.")
        elif user.logged_in:
            displayname = f"@{user.username}" if user.username else user.displayname
            return await reply("That user seems to be logged in. "
                               f"Just invite [{displayname}](tg://user?id={user.tgid})")
        else:
            await portal.main_intent.invite(portal.mxid, user.mxid)
            return await reply(f"Invited `{user.mxid}` to the portal.")

    async def handle_command(self, message):
        def reply(reply_text):
            return self.client.send_message_super(message.to_id, reply_text)

        text = message.message
        portal = po.Portal.get_by_entity(message.to_id)
        if text == "/portal" or text == f"/portal@{self.username}":
            await self.handle_command_portal(portal, reply)
        elif text.startswith("/invite") or text.startswith(f"/invite@{self.username}"):
            await self.handle_command_invite(portal, reply, mxid=text[len("/invite "):])

    async def update(self, update):
        if not isinstance(update, (UpdateNewMessage, UpdateNewChannelMessage)):
            return

        is_command = (isinstance(update.message, Message)
                      and update.message.entities and len(update.message.entities) > 0
                      and isinstance(update.message.entities[0], MessageEntityBotCommand))
        if is_command:
            return await self.handle_command(update.message)

        if not isinstance(update.message, MessageService):
            return

        to_id = update.message.to_id
        if isinstance(to_id, PeerChannel):
            to_id = to_id.channel_id
            type = "channel"
        elif isinstance(to_id, PeerChat):
            to_id = to_id.chat_id
            type = "chat"
        else:
            return

        action = update.message.action
        if isinstance(action, MessageActionChatAddUser) and self.tgid in action.users:
            self.add_chat(to_id, type)
        elif isinstance(action, MessageActionChatDeleteUser) and action.user_id == self.tgid:
            self.remove_chat(to_id)

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
