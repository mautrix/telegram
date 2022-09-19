# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2022 Tulir Asokan
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
from __future__ import annotations

from typing import Awaitable, Callable, Literal
import logging
import time

from telethon.errors import ChannelInvalidError, ChannelPrivateError
from telethon.tl.functions.channels import GetChannelsRequest, GetParticipantRequest
from telethon.tl.functions.messages import GetChatsRequest, GetFullChatRequest
from telethon.tl.patched import Message, MessageService
from telethon.tl.types import (
    ChannelParticipantAdmin,
    ChannelParticipantCreator,
    ChatForbidden,
    ChatParticipantAdmin,
    ChatParticipantCreator,
    ChatParticipantsForbidden,
    InputChannel,
    InputUser,
    MessageActionChatAddUser,
    MessageActionChatDeleteUser,
    MessageActionChatMigrateTo,
    MessageEntityBotCommand,
    PeerChannel,
    PeerChat,
    PeerUser,
    TypeChannelParticipant,
    TypeChatParticipant,
    TypeInputPeer,
    TypePeer,
    UpdateNewChannelMessage,
    UpdateNewMessage,
    User,
)
from telethon.utils import add_surrogate, del_surrogate

from mautrix.errors import MBadState, MForbidden
from mautrix.types import RoomID, UserID

from . import portal as po, puppet as pu, user as u
from .abstract_user import AbstractUser
from .db import BotChat, Message as DBMessage
from .types import TelegramID

ReplyFunc = Callable[[str], Awaitable[Message]]
BanFunc = Callable[[RoomID, UserID, str], Awaitable[None]]
TelegramAdminPermission = Literal[
    "change_info",
    "post_messages",
    "edit_messages",
    "delete_messages",
    "ban_users",
    "invite_users",
    "pin_messages",
    "add_admins",
    "anonymous",
    "manage_call",
    "other",
]


class Bot(AbstractUser):
    log: logging.Logger = logging.getLogger("mau.user.bot")

    token: str
    chats: dict[int, str]
    tg_whitelist: list[int]
    whitelist_group_admins: bool
    _me_info: User | None
    _me_mxid: UserID | None
    _admin_cache: dict[
        tuple[int, int],
        tuple[ChatParticipantAdmin | ChatParticipantCreator | None, float],
    ]
    required_permissions: dict[str, TelegramAdminPermission] = {
        "portal": None,
        "invite": "invite_users",
        "mxban": "ban_users",
        "mxkick": "ban_users",
    }

    def __init__(self, token: str) -> None:
        super().__init__()
        self.token = token
        self.tgid = None
        self.mxid = None
        self.puppet_whitelisted = True
        self.whitelisted = True
        self.relaybot_whitelisted = True
        self.tg_username = None
        self.is_relaybot = True
        self.is_bot = True
        self.chats = {}
        self._admin_cache = {}
        self.tg_whitelist = []
        self.whitelist_group_admins = (
            self.config["bridge.relaybot.whitelist_group_admins"] or False
        )
        self._me_info = None
        self._me_mxid = None
        self._login_wait_fut = self.loop.create_future()

    async def get_me(self, use_cache: bool = True) -> tuple[User, UserID]:
        if not use_cache or not self._me_mxid:
            self._me_info = await self.client.get_me()
            self._me_mxid = pu.Puppet.get_mxid_from_id(TelegramID(self._me_info.id))
        return self._me_info, self._me_mxid

    async def init_permissions(self) -> None:
        whitelist = self.config["bridge.relaybot.whitelist"] or []
        for user_id in whitelist:
            if isinstance(user_id, str):
                entity = await self.client.get_input_entity(user_id)
                if isinstance(entity, InputUser):
                    user_id = entity.user_id
                else:
                    user_id = None
            if isinstance(user_id, int):
                self.tg_whitelist.append(user_id)

    async def start(self, delete_unless_authenticated: bool = False) -> Bot:
        self.chats = {chat.id: chat.type for chat in await BotChat.all()}
        await super().start(delete_unless_authenticated)
        if not await self.is_logged_in():
            await self.client.sign_in(bot_token=self.token)
        await self.post_login()
        return self

    async def post_login(self) -> None:
        await self.init_permissions()
        info = await self.client.get_me()
        self.tgid = TelegramID(info.id)
        self.tg_username = info.username
        self.mxid = pu.Puppet.get_mxid_from_id(self.tgid)
        self._login_wait_fut.set_result(None)
        self._login_wait_fut = None

        chat_ids = [chat_id for chat_id, chat_type in self.chats.items() if chat_type == "chat"]
        response = await self.client(GetChatsRequest(chat_ids))
        for chat in response.chats:
            if isinstance(chat, ChatForbidden) or chat.left or chat.deactivated:
                await self.remove_chat(TelegramID(chat.id))

        channel_ids = [
            InputChannel(chat_id, 0)
            for chat_id, chat_type in self.chats.items()
            if chat_type == "channel"
        ]
        for channel_id in channel_ids:
            try:
                await self.client(GetChannelsRequest([channel_id]))
            except (ChannelPrivateError, ChannelInvalidError):
                await self.remove_chat(TelegramID(channel_id.channel_id))

    async def register_portal(self, portal: po.Portal) -> None:
        await self.add_chat(portal.tgid, portal.peer_type)

    async def unregister_portal(self, tgid: TelegramID, tg_receiver: TelegramID) -> None:
        await self.remove_chat(tgid)

    async def add_chat(self, chat_id: TelegramID, chat_type: str) -> None:
        if chat_id not in self.chats:
            self.chats[chat_id] = chat_type
            await BotChat(id=chat_id, type=chat_type).insert()

    async def remove_chat(self, chat_id: TelegramID) -> None:
        try:
            del self.chats[chat_id]
        except KeyError:
            pass
        await BotChat.delete_by_id(chat_id)

    async def _get_admin_participant(
        self, chat: TypePeer | TypeInputPeer, tgid: TelegramID
    ) -> TypeChatParticipant | TypeChannelParticipant | None:
        chan_id = chat.channel_id if isinstance(chat, PeerChannel) else chat.chat_id
        try:
            cached, created = self._admin_cache[chan_id, tgid]
            if created + 60 < time.time():
                return cached
        except KeyError:
            pass
        if isinstance(chat, PeerChannel):
            p = await self.client(GetParticipantRequest(chat, tgid))
            pcp = p.participant
            self._admin_cache[chat.channel_id, tgid] = (pcp, time.time())
            return pcp
        elif isinstance(chat, PeerChat):
            chat = await self.client(GetFullChatRequest(chat.chat_id))
            if isinstance(chat.full_chat.participants, ChatParticipantsForbidden):
                return None
            participants = chat.full_chat.participants.participants
            for p in participants:
                self._admin_cache[chat.channel_id, tgid] = (p, time.time())
                if p.user_id == tgid:
                    return p
        return None

    @staticmethod
    def _has_participant_permission(
        pcp: TypeChatParticipant | TypeChannelParticipant | None,
        permission: TelegramAdminPermission | None,
    ) -> bool:
        if isinstance(pcp, (ChannelParticipantCreator, ChannelParticipantAdmin)):
            return permission is None or getattr(pcp.admin_rights, permission, False)
        elif isinstance(pcp, (ChatParticipantCreator, ChatParticipantAdmin)):
            return True
        return False

    async def _can_use_commands(
        self, chat: TypePeer, tgid: TelegramID, permission: TelegramAdminPermission | None = None
    ) -> bool:
        if tgid in self.tg_whitelist:
            return True

        user = await u.User.get_by_tgid(tgid)
        if user and user.is_admin:
            self.tg_whitelist.append(user.tgid)
            return True

        if self.whitelist_group_admins:
            pcp = await self._get_admin_participant(chat, tgid)
            return self._has_participant_permission(pcp, permission)
        return False

    async def check_can_use_command(self, event: Message, reply: ReplyFunc, command: str) -> bool:
        if command not in self.required_permissions:
            # Unknown command
            return False
        elif not isinstance(event.from_id, PeerUser):
            await reply("Channels can't use commands")
            return False
        elif not await self._can_use_commands(
            event.to_id, TelegramID(event.from_id.user_id), self.required_permissions[command]
        ):
            await reply("You do not have the permission to use that command.")
            return False
        return True

    async def handle_command_portal(self, portal: po.Portal, reply: ReplyFunc) -> Message:
        if not self.config["bridge.relaybot.authless_portals"]:
            return await reply("This bridge doesn't allow portal creation from Telegram.")

        if not portal.allow_bridging:
            return await reply("This bridge doesn't allow bridging this chat.")

        await portal.create_matrix_room(self)
        if portal.mxid:
            if portal.username:
                return await reply(
                    f"Portal is public: [{portal.alias}](https://matrix.to/#/{portal.alias})"
                )
            else:
                return await reply("Portal is not public. Use `/invite <mxid>` to get an invite.")
        else:
            return await reply("Couldn't create portal room")

    async def handle_command_invite(
        self, portal: po.Portal, reply: ReplyFunc, mxid_input: UserID
    ) -> Message:
        if len(mxid_input) == 0:
            return await reply("Usage: `/invite <mxid>`")
        elif not portal.mxid:
            return await reply("Portal does not have Matrix room. Create one with /portal first.")
        if mxid_input[0] != "@" or mxid_input.find(":") < 2:
            return await reply("That doesn't look like a Matrix ID.")
        user = await u.User.get_and_start_by_mxid(mxid_input)
        if not user.relaybot_whitelisted:
            return await reply("That user is not whitelisted to use the bridge.")
        elif await user.is_logged_in():
            displayname = f"@{user.tg_username}" if user.tg_username else user.displayname
            return await reply(
                "That user seems to be logged in. "
                f"Just invite [{displayname}](tg://user?id={user.tgid})"
            )
        else:
            try:
                await portal.invite_to_matrix(user.mxid)
            except MBadState:
                try:
                    await portal.main_intent.unban_user(
                        portal.mxid, user.mxid, reason="Invited from Telegram"
                    )
                except Exception:
                    return await reply(f"Failed to unban `{user.mxid}` from the portal.")
                await portal.invite_to_matrix(user.mxid)
                return await reply(f"Unbanned and invited `{user.mxid}` to the portal.")
            return await reply(f"Invited `{user.mxid}` to the portal.")

    async def handle_command_ban(
        self,
        message: Message,
        portal: po.Portal,
        reply: ReplyFunc,
        reason: str,
        action: Literal["kick", "ban"] = "ban",
    ) -> Message:
        if not message.reply_to:
            return await reply("You must reply to a relaybot message when using that command")
        reply_to_id = TelegramID(message.reply_to.reply_to_msg_id)
        tg_space = portal.tgid if portal.peer_type == "channel" else self.tgid
        msg = await DBMessage.get_one_by_tgid(reply_to_id, tg_space)
        if not msg or msg.sender != self.tgid or not msg.sender_mxid:
            return await reply("Target message is not a relayed message")
        puppet = await pu.Puppet.get_by_peer(message.from_id)
        actioned = "Banned" if action == "ban" else "Kicked"
        try:
            intent = puppet.intent_for(portal)
            func: BanFunc = intent.ban_user if action == "ban" else intent.kick_user
            await func(portal.mxid, msg.sender_mxid, reason)
        except MForbidden as e:
            self.log.warning(
                f"Failed to {action} {msg.sender_mxid} from {portal.mxid} as {puppet.mxid}: {e}, "
                f"falling back to bridge bot"
            )
            reason_prefix = f"{actioned} by {puppet.displayname or puppet.tgid}"
            reason = f"{reason_prefix}: {reason}" if reason else reason_prefix
            try:
                func: BanFunc = (
                    self.az.intent.ban_user if action == "ban" else self.az.intent.kick_user
                )
                await func(portal.mxid, msg.sender_mxid, reason)
            except MForbidden as e:
                self.log.warning(
                    f"Failed to {action} {msg.sender_mxid} from {portal.mxid} as bridge bot: {e}"
                )
                return await reply(f"Failed to {action} `{msg.sender_mxid}`")
        return await reply(f"Successfully {actioned.lower()} `{msg.sender_mxid}`")

    @staticmethod
    def handle_command_id(message: Message, reply: ReplyFunc) -> Awaitable[Message]:
        # Provide the prefixed ID to the user so that the user wouldn't need to specify whether the
        # chat is a normal group or a supergroup/channel when using the ID.
        if isinstance(message.to_id, PeerChannel):
            return reply(f"-100{message.to_id.channel_id}")
        elif isinstance(message.to_id, PeerChat):
            return reply(str(-message.to_id.chat_id))
        elif isinstance(message.to_id, PeerUser):
            return reply(
                f"Your user ID is {message.to_id.user_id}.\n\n"
                f"If you're trying to bridge a group chat to Matrix, you must run the command in "
                f"the group, not here. **The ID above will not work** with `!tg bridge`."
            )
        else:
            return reply("Failed to find chat ID.")

    def parse_command(self, message: Message) -> tuple[str | None, str | None]:
        if not message.entities or len(message.entities) < 1 or not message.message:
            return None, None
        cmd_entity = message.entities[0]
        if not isinstance(cmd_entity, MessageEntityBotCommand) or cmd_entity.offset != 0:
            return None, None
        surrogated_text = add_surrogate(message.message)
        command: str = del_surrogate(surrogated_text[: cmd_entity.length]).lower()
        rest_of_message: str = ""
        if len(surrogated_text) > cmd_entity.length + 1:
            rest_of_message: str = del_surrogate(surrogated_text[cmd_entity.length + 1 :])
        command, *target = command.split("@", 1)
        if not command.startswith("/"):
            return None, None
        elif target and target[0] != self.tg_username.lower():
            return None, None
        return command[1:], rest_of_message

    async def handle_command(self, message: Message, command: str, args: str) -> None:
        def reply(reply_text: str) -> Awaitable[Message]:
            return self.client.send_message(message.chat_id, reply_text, reply_to=message.id)

        if command == "start":
            pcm = self.config["bridge.relaybot.private_chat.message"]
            if pcm:
                await reply(pcm)
        elif command == "id":
            await self.handle_command_id(message, reply)
        elif not message.is_private:
            if not await self.check_can_use_command(message, reply, command):
                return
            portal = await po.Portal.get_by_entity(message.to_id)
            if command == "portal":
                await self.handle_command_portal(portal, reply)
            elif command == "invite":
                await self.handle_command_invite(portal, reply, mxid_input=UserID(args))
            elif command == "mxban":
                await self.handle_command_ban(message, portal, reply, reason=args)
            elif command == "mxkick":
                await self.handle_command_ban(message, portal, reply, reason=args, action="kick")

    async def handle_service_message(self, message: MessageService) -> None:
        to_peer = message.to_id
        if isinstance(to_peer, PeerChannel):
            to_id = TelegramID(to_peer.channel_id)
            chat_type = "channel"
        elif isinstance(to_peer, PeerChat):
            to_id = TelegramID(to_peer.chat_id)
            chat_type = "chat"
        else:
            return

        action = message.action
        if isinstance(action, MessageActionChatAddUser) and self.tgid in action.users:
            await self.add_chat(to_id, chat_type)
        elif isinstance(action, MessageActionChatDeleteUser) and action.user_id == self.tgid:
            await self.remove_chat(to_id)
        elif isinstance(action, MessageActionChatMigrateTo):
            await self.remove_chat(to_id)
            await self.add_chat(TelegramID(action.channel_id), "channel")

    async def update(self, update) -> bool:
        if self._login_wait_fut:
            await self._login_wait_fut
        if not isinstance(update, (UpdateNewMessage, UpdateNewChannelMessage)):
            return False
        if isinstance(update.message, MessageService):
            await self.handle_service_message(update.message)
            return False

        if isinstance(update.message, Message):
            command, args = self.parse_command(update.message)
            if command:
                await self.handle_command(update.message, command, args)
        return False

    def is_in_chat(self, peer_id) -> bool:
        return peer_id in self.chats

    @property
    def name(self) -> str:
        return "bot"
