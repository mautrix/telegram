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
from typing import Awaitable, Callable, Dict, List, Optional, Pattern, Tuple, TYPE_CHECKING
import logging
import re

from telethon.tl.patched import Message, MessageService
from telethon.tl.types import (
    ChannelParticipantAdmin, ChannelParticipantCreator, ChatForbidden, ChatParticipantAdmin,
    ChatParticipantCreator, InputChannel, InputUser, MessageActionChatAddUser,
    MessageActionChatDeleteUser, MessageEntityBotCommand, PeerChannel, PeerChat, TypePeer,
    UpdateNewChannelMessage, UpdateNewMessage, MessageActionChatMigrateTo, User)
from telethon.tl.functions.messages import GetChatsRequest, GetFullChatRequest
from telethon.tl.functions.channels import GetChannelsRequest, GetParticipantRequest
from telethon.errors import ChannelInvalidError, ChannelPrivateError

from .types import MatrixUserID
from .abstract_user import AbstractUser
from .db import BotChat
from .types import TelegramID
from . import puppet as pu, portal as po, user as u

if TYPE_CHECKING:
    from .config import Config
    from .context import Context

config = None  # type: Config

ReplyFunc = Callable[[str], Awaitable[Message]]


class Bot(AbstractUser):
    log = logging.getLogger("mau.bot")  # type: logging.Logger
    mxid_regex = re.compile("@.+:.+")  # type: Pattern

    def __init__(self, token: str) -> None:
        super().__init__()
        self.token = token  # type: str
        self.puppet_whitelisted = True  # type: bool
        self.whitelisted = True  # type: bool
        self.relaybot_whitelisted = True  # type: bool
        self.username = None  # type: str
        self.is_relaybot = True  # type: bool
        self.is_bot = True  # type: bool
        self.chats = {}  # type: Dict[int, str]
        self.tg_whitelist = []  # type: List[int]
        self.whitelist_group_admins = (config["bridge.relaybot.whitelist_group_admins"]
                                       or False)  # type: bool
        self._me_info = None  # type: Optional[User]
        self._me_mxid = None  # type: Optional[MatrixUserID]

    async def get_me(self, use_cache: bool = True) -> Tuple[User, MatrixUserID]:
        if not use_cache or not self._me_mxid:
            self._me_info = await self.client.get_me()
            self._me_mxid = pu.Puppet.get_mxid_from_id(TelegramID(self._me_info.id))
        return self._me_info, self._me_mxid

    async def init_permissions(self) -> None:
        whitelist = config["bridge.relaybot.whitelist"] or []
        for user_id in whitelist:
            if isinstance(user_id, str):
                entity = await self.client.get_input_entity(user_id)
                if isinstance(entity, InputUser):
                    user_id = entity.user_id
                else:
                    user_id = None
            if isinstance(user_id, int):
                self.tg_whitelist.append(user_id)

    async def start(self, delete_unless_authenticated: bool = False) -> 'Bot':
        self.chats = {chat.id: chat.type for chat in BotChat.all()}
        await super().start(delete_unless_authenticated)
        if not await self.is_logged_in():
            await self.client.sign_in(bot_token=self.token)
        await self.post_login()
        return self

    async def post_login(self) -> None:
        await self.init_permissions()
        info = await self.client.get_me()
        self.tgid = info.id
        self.username = info.username
        self.mxid = pu.Puppet.get_mxid_from_id(self.tgid)

        chat_ids = [chat_id for chat_id, chat_type in self.chats.items() if chat_type == "chat"]
        response = await self.client(GetChatsRequest(chat_ids))
        for chat in response.chats:
            if isinstance(chat, ChatForbidden) or chat.left or chat.deactivated:
                self.remove_chat(TelegramID(chat.id))

        channel_ids = [InputChannel(chat_id, 0)
                       for chat_id, chat_type in self.chats.items()
                       if chat_type == "channel"]
        for channel_id in channel_ids:
            try:
                await self.client(GetChannelsRequest([channel_id]))
            except (ChannelPrivateError, ChannelInvalidError):
                self.remove_chat(TelegramID(channel_id.channel_id))

        if config["bridge.catch_up"]:
            try:
                await self.client.catch_up()
            except Exception:
                self.log.exception("Failed to run catch_up() for bot")

    def register_portal(self, portal: po.Portal) -> None:
        self.add_chat(portal.tgid, portal.peer_type)

    def unregister_portal(self, portal: po.Portal) -> None:
        self.remove_chat(portal.tgid)

    def add_chat(self, chat_id: TelegramID, chat_type: str) -> None:
        if chat_id not in self.chats:
            self.chats[chat_id] = chat_type
            BotChat(id=TelegramID(chat_id), type=chat_type).insert()

    def remove_chat(self, chat_id: TelegramID) -> None:
        try:
            del self.chats[chat_id]
        except KeyError:
            pass
        BotChat.delete(chat_id)

    async def _can_use_commands(self, chat: TypePeer, tgid: TelegramID) -> bool:
        if tgid in self.tg_whitelist:
            return True

        user = u.User.get_by_tgid(tgid)
        if user and user.is_admin:
            self.tg_whitelist.append(user.tgid)
            return True

        if self.whitelist_group_admins:
            if isinstance(chat, PeerChannel):
                p = await self.client(GetParticipantRequest(chat, tgid))
                return isinstance(p, (ChannelParticipantCreator, ChannelParticipantAdmin))
            elif isinstance(chat, PeerChat):
                chat = await self.client(GetFullChatRequest(chat.chat_id))
                participants = chat.full_chat.participants.participants
                for p in participants:
                    if p.user_id == tgid:
                        return isinstance(p, (ChatParticipantCreator, ChatParticipantAdmin))
        return False

    async def check_can_use_commands(self, event: Message, reply: ReplyFunc) -> bool:
        if not await self._can_use_commands(event.to_id, event.from_id):
            await reply("You do not have the permission to use that command.")
            return False
        return True

    async def handle_command_portal(self, portal: po.Portal, reply: ReplyFunc) -> Message:
        if not config["bridge.relaybot.authless_portals"]:
            return await reply("This bridge doesn't allow portal creation from Telegram.")

        if not portal.allow_bridging():
            return await reply("This bridge doesn't allow bridging this chat.")

        await portal.create_matrix_room(self)
        if portal.mxid:
            if portal.username:
                return await reply(
                    f"Portal is public: [{portal.alias}](https://matrix.to/#/{portal.alias})")
            else:
                return await reply(
                    "Portal is not public. Use `/invite <mxid>` to get an invite.")

    async def handle_command_invite(self, portal: po.Portal, reply: ReplyFunc,
                                    mxid_input: MatrixUserID) -> Message:
        if len(mxid_input) == 0:
            return await reply("Usage: `/invite <mxid>`")
        elif not portal.mxid:
            return await reply("Portal does not have Matrix room. "
                               "Create one with /portal first.")
        if not self.mxid_regex.match(mxid_input):
            return await reply("That doesn't look like a Matrix ID.")
        user = await u.User.get_by_mxid(MatrixUserID(mxid_input)).ensure_started()
        if not user.relaybot_whitelisted:
            return await reply("That user is not whitelisted to use the bridge.")
        elif await user.is_logged_in():
            displayname = f"@{user.username}" if user.username else user.displayname
            return await reply("That user seems to be logged in. "
                               f"Just invite [{displayname}](tg://user?id={user.tgid})")
        else:
            await portal.main_intent.invite(portal.mxid, user.mxid)
            return await reply(f"Invited `{user.mxid}` to the portal.")

    @staticmethod
    def handle_command_id(message: Message, reply: ReplyFunc) -> Awaitable[Message]:
        # Provide the prefixed ID to the user so that the user wouldn't need to specify whether the
        # chat is a normal group or a supergroup/channel when using the ID.
        if isinstance(message.to_id, PeerChannel):
            return reply(f"-100{message.to_id.channel_id}")
        return reply(str(-message.to_id.chat_id))

    def match_command(self, text: str, command: str) -> bool:
        text = text.lower()
        command = f"/{command.lower()}"
        command_targeted = f"{command}@{self.username.lower()}"

        is_plain_command = text == command or text == command_targeted
        if is_plain_command:
            return True

        is_arg_command = text.startswith(command + " ") or text.startswith(command_targeted + " ")
        if is_arg_command:
            return True

        return False

    async def handle_command(self, message: Message) -> None:
        def reply(reply_text: str) -> Awaitable[Message]:
            return self.client.send_message(message.to_id, reply_text, reply_to=message.id)

        text = message.message

        if self.match_command(text, "id"):
            await self.handle_command_id(message, reply)
            return

        portal = po.Portal.get_by_entity(message.to_id)

        if self.match_command(text, "portal"):
            if not await self.check_can_use_commands(message, reply):
                return
            await self.handle_command_portal(portal, reply)
        elif self.match_command(text, "invite"):
            if not await self.check_can_use_commands(message, reply):
                return
            try:
                mxid = text[text.index(" ") + 1:]
            except ValueError:
                mxid = ""
            await self.handle_command_invite(portal, reply, mxid_input=mxid)

    def handle_service_message(self, message: MessageService) -> None:
        to_id = message.to_id  # type: TelegramID
        if isinstance(to_id, PeerChannel):
            to_id = to_id.channel_id
            chat_type = "channel"
        elif isinstance(to_id, PeerChat):
            to_id = to_id.chat_id
            chat_type = "chat"
        else:
            return

        action = message.action
        if isinstance(action, MessageActionChatAddUser) and self.tgid in action.users:
            self.add_chat(to_id, chat_type)
        elif isinstance(action, MessageActionChatDeleteUser) and action.user_id == self.tgid:
            self.remove_chat(to_id)
        elif isinstance(action, MessageActionChatMigrateTo):
            self.remove_chat(to_id)
            self.add_chat(TelegramID(action.channel_id), "channel")

    async def update(self, update) -> bool:
        if not isinstance(update, (UpdateNewMessage, UpdateNewChannelMessage)):
            return False
        if isinstance(update.message, MessageService):
            self.handle_service_message(update.message)
            return False

        is_command = (isinstance(update.message, Message)
                      and update.message.entities and len(update.message.entities) > 0
                      and isinstance(update.message.entities[0], MessageEntityBotCommand))
        if is_command:
            await self.handle_command(update.message)
            return True
        return False

    def is_in_chat(self, peer_id) -> bool:
        return peer_id in self.chats

    @property
    def name(self) -> str:
        return "bot"


def init(cfg: 'Config') -> Optional[Bot]:
    global config
    config = cfg
    token = config["telegram.bot_token"]
    if token and not token.lower().startswith("disable"):
        return Bot(token)
    return None
