# -*- coding: future_fstrings -*-
# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2018 Tulir Asokan
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
from typing import Tuple, Optional, List, Union, TYPE_CHECKING
from abc import ABC, abstractmethod
import asyncio
import logging
import platform

from sqlalchemy import orm
from telethon.tl.types import Channel, ChannelForbidden, Chat, ChatForbidden, Message, \
    MessageActionChannelMigrateFrom, MessageService, PeerUser, TypeUpdate, \
    UpdateChannelPinnedMessage, UpdateChatAdmins, UpdateChatParticipantAdmin, \
    UpdateChatParticipants, UpdateChatUserTyping, UpdateDeleteChannelMessages, \
    UpdateDeleteMessages, UpdateEditChannelMessage, UpdateEditMessage, UpdateNewChannelMessage, \
    UpdateNewMessage, UpdateReadHistoryOutbox, UpdateShortChatMessage, UpdateShortMessage, \
    UpdateUserName, UpdateUserPhoto, UpdateUserStatus, UpdateUserTyping, User, UserStatusOffline, \
    UserStatusOnline

from mautrix_appservice import MatrixRequestError, AppService
from alchemysession import AlchemySessionContainer

from . import portal as po, puppet as pu, __version__
from .db import Message as DBMessage
from .types import TelegramID, MatrixUserID
from .tgclient import MautrixTelegramClient

if TYPE_CHECKING:
    from .context import Context
    from .config import Config
    from .bot import Bot

config = None  # type: Config
# Value updated from config in init()
MAX_DELETIONS = 10  # type: int

UpdateMessage = Union[UpdateShortChatMessage, UpdateShortMessage, UpdateNewChannelMessage,
                      UpdateNewMessage, UpdateEditMessage, UpdateEditChannelMessage]
UpdateMessageContent = Union[UpdateShortMessage, UpdateShortChatMessage, Message, MessageService]


class AbstractUser(ABC):
    session_container = None  # type: AlchemySessionContainer
    loop = None  # type: asyncio.AbstractEventLoop
    log = None  # type: logging.Logger
    db = None  # type: orm.Session
    az = None  # type: AppService
    bot = None  # type: Bot
    ignore_incoming_bot_events = True  # type: bool

    def __init__(self) -> None:
        self.is_admin = False  # type: bool
        self.matrix_puppet_whitelisted = False  # type: bool
        self.puppet_whitelisted = False  # type: bool
        self.whitelisted = False  # type: bool
        self.relaybot_whitelisted = False  # type: bool
        self.client = None  # type: MautrixTelegramClient
        self.tgid = None  # type: TelegramID
        self.mxid = None  # type: MatrixUserID
        self.is_relaybot = False  # type: bool
        self.is_bot = False  # type: bool
        self.relaybot = None  # type: Optional[Bot]

    @property
    def connected(self) -> bool:
        return self.client and self.client.is_connected()

    @property
    def _proxy_settings(self) -> Optional[Tuple[int, str, str, str, str, str]]:
        proxy_type = config["telegram.proxy.type"].lower()
        if proxy_type == "disabled":
            return None
        elif proxy_type == "socks4":
            proxy_type = 1
        elif proxy_type == "socks5":
            proxy_type = 2
        elif proxy_type == "http":
            proxy_type = 3

        return (proxy_type,
                config["telegram.proxy.address"], config["telegram.proxy.port"],
                config["telegram.proxy.rdns"],
                config["telegram.proxy.username"], config["telegram.proxy.password"])

    def _init_client(self) -> None:
        self.log.debug(f"Initializing client for {self.name}")
        device = f"{platform.system()} {platform.release()}"
        sysversion = MautrixTelegramClient.__version__
        self.session = self.session_container.new_session(self.name)
        self.client = MautrixTelegramClient(session=self.session,
                                            api_id=config["telegram.api_id"],
                                            api_hash=config["telegram.api_hash"],
                                            loop=self.loop,
                                            app_version=__version__,
                                            system_version=sysversion,
                                            device_model=device,
                                            timeout=120,
                                            proxy=self._proxy_settings)
        self.client.add_event_handler(self._update_catch)

    @abstractmethod
    async def update(self, update: TypeUpdate) -> bool:
        return False

    @abstractmethod
    async def post_login(self) -> None:
        raise NotImplementedError()

    @abstractmethod
    def register_portal(self, portal: po.Portal) -> None:
        raise NotImplementedError()

    @abstractmethod
    def unregister_portal(self, portal: po.Portal) -> None:
        raise NotImplementedError()

    async def _update_catch(self, update: TypeUpdate) -> None:
        try:
            if not await self.update(update):
                await self._update(update)
        except Exception:
            self.log.exception("Failed to handle Telegram update")

    async def get_dialogs(self, limit: int = None) -> List[Union[Chat, Channel]]:
        if self.is_bot:
            return []
        dialogs = await self.client.get_dialogs(limit=limit)
        return [dialog.entity for dialog in dialogs if (
            not isinstance(dialog.entity, (User, ChatForbidden, ChannelForbidden))
            and not (isinstance(dialog.entity, Chat)
                     and (dialog.entity.deactivated or dialog.entity.left)))]

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError()

    async def is_logged_in(self) -> bool:
        return self.client and self.client.is_connected() and await self.client.is_user_authorized()

    async def has_full_access(self, allow_bot: bool = False) -> bool:
        return (self.puppet_whitelisted
                and (not self.is_bot or allow_bot)
                and await self.is_logged_in())

    async def start(self, delete_unless_authenticated: bool = False) -> 'AbstractUser':
        if not self.client:
            self._init_client()
        await self.client.connect()
        self.log.debug("%s connected: %s", self.mxid, self.connected)
        return self

    async def ensure_started(self, even_if_no_session=False) -> 'AbstractUser':
        if not self.puppet_whitelisted:
            return self
        self.log.debug("ensure_started(%s, connected=%s, even_if_no_session=%s, session_count=%s)",
                       self.mxid, self.connected, even_if_no_session,
                       self.session_container.Session.query.filter(
                           self.session_container.Session.session_id == self.mxid).count())
        should_connect = (even_if_no_session or
                          self.session_container.Session.query.filter(
                              self.session_container.Session.session_id == self.mxid).count() > 0)
        if not self.connected and should_connect:
            await self.start(delete_unless_authenticated=not even_if_no_session)
        return self

    async def stop(self) -> None:
        await self.client.disconnect()
        self.client = None

    # region Telegram update handling

    async def _update(self, update: TypeUpdate) -> None:
        if isinstance(update, (UpdateShortChatMessage, UpdateShortMessage, UpdateNewChannelMessage,
                               UpdateNewMessage, UpdateEditMessage, UpdateEditChannelMessage)):
            await self.update_message(update)
        elif isinstance(update, UpdateDeleteMessages):
            await self.delete_message(update)
        elif isinstance(update, UpdateDeleteChannelMessages):
            await self.delete_channel_message(update)
        elif isinstance(update, (UpdateChatUserTyping, UpdateUserTyping)):
            await self.update_typing(update)
        elif isinstance(update, UpdateUserStatus):
            await self.update_status(update)
        elif isinstance(update, (UpdateChatAdmins, UpdateChatParticipantAdmin)):
            await self.update_admin(update)
        elif isinstance(update, UpdateChatParticipants):
            await self.update_participants(update)
        elif isinstance(update, UpdateChannelPinnedMessage):
            await self.update_pinned_messages(update)
        elif isinstance(update, (UpdateUserName, UpdateUserPhoto)):
            await self.update_others_info(update)
        elif isinstance(update, UpdateReadHistoryOutbox):
            await self.update_read_receipt(update)
        else:
            self.log.debug("Unhandled update: %s", update)

    @staticmethod
    async def update_pinned_messages(update: UpdateChannelPinnedMessage) -> None:
        portal = po.Portal.get_by_tgid(TelegramID(update.channel_id))
        if portal and portal.mxid:
            await portal.receive_telegram_pin_id(update.id)

    @staticmethod
    async def update_participants(update: UpdateChatParticipants) -> None:
        portal = po.Portal.get_by_tgid(TelegramID(update.participants.chat_id))
        if portal and portal.mxid:
            await portal.update_telegram_participants(update.participants.participants)

    async def update_read_receipt(self, update: UpdateReadHistoryOutbox) -> None:
        if not isinstance(update.peer, PeerUser):
            self.log.debug("Unexpected read receipt peer: %s", update.peer)
            return

        portal = po.Portal.get_by_tgid(TelegramID(update.peer.user_id), self.tgid)
        if not portal or not portal.mxid:
            return

        # We check that these are user read receipts, so tg_space is always the user ID.
        message = DBMessage.get_by_tgid(update.max_id, self.tgid)
        if not message:
            return

        puppet = pu.Puppet.get(TelegramID(update.peer.user_id))
        await puppet.intent.mark_read(portal.mxid, message.mxid)

    async def update_admin(self,
                           update: Union[UpdateChatAdmins, UpdateChatParticipantAdmin]) -> None:
        # TODO duplication not checked
        portal = po.Portal.get_by_tgid(TelegramID(update.chat_id), peer_type="chat")
        if not portal or not portal.mxid:
            return

        if isinstance(update, UpdateChatAdmins):
            await portal.set_telegram_admins_enabled(update.enabled)
        elif isinstance(update, UpdateChatParticipantAdmin):
            await portal.set_telegram_admin(TelegramID(update.user_id))
        else:
            self.log.warning("Unexpected admin status update: %s", update)

    async def update_typing(self, update: Union[UpdateUserTyping, UpdateChatUserTyping]) -> None:
        if isinstance(update, UpdateUserTyping):
            portal = po.Portal.get_by_tgid(TelegramID(update.user_id), self.tgid, "user")
        else:
            portal = po.Portal.get_by_tgid(TelegramID(update.chat_id), peer_type="chat")

        if not portal or not portal.mxid:
            return

        sender = pu.Puppet.get(TelegramID(update.user_id))
        await portal.handle_telegram_typing(sender, update)

    async def update_others_info(self, update: Union[UpdateUserName, UpdateUserPhoto]) -> None:
        # TODO duplication not checked
        puppet = pu.Puppet.get(TelegramID(update.user_id))
        if isinstance(update, UpdateUserName):
            if await puppet.update_displayname(self, update):
                puppet.save()
        elif isinstance(update, UpdateUserPhoto):
            if await puppet.update_avatar(self, update.photo.photo_big):
                puppet.save()
        else:
            self.log.warning("Unexpected other user info update: %s", update)

    async def update_status(self, update: UpdateUserStatus) -> None:
        puppet = pu.Puppet.get(TelegramID(update.user_id))
        if isinstance(update.status, UserStatusOnline):
            await puppet.default_mxid_intent.set_presence("online")
        elif isinstance(update.status, UserStatusOffline):
            await puppet.default_mxid_intent.set_presence("offline")
        else:
            self.log.warning("Unexpected user status update: %s", update)
        return

    def get_message_details(self, update: UpdateMessage) -> Tuple[UpdateMessageContent,
                                                                  Optional[pu.Puppet],
                                                                  Optional[po.Portal]]:
        if isinstance(update, UpdateShortChatMessage):
            portal = po.Portal.get_by_tgid(TelegramID(update.chat_id), peer_type="chat")
            sender = pu.Puppet.get(TelegramID(update.from_id))
        elif isinstance(update, UpdateShortMessage):
            portal = po.Portal.get_by_tgid(TelegramID(update.user_id), self.tgid, "user")
            sender = pu.Puppet.get(self.tgid if update.out else update.user_id)
        elif isinstance(update, (UpdateNewMessage, UpdateNewChannelMessage,
                                 UpdateEditMessage, UpdateEditChannelMessage)):
            update = update.message
            if isinstance(update.to_id, PeerUser) and not update.out:
                portal = po.Portal.get_by_tgid(update.from_id, peer_type="user",
                                               tg_receiver=self.tgid)
            else:
                portal = po.Portal.get_by_entity(update.to_id, receiver_id=self.tgid)
            sender = pu.Puppet.get(update.from_id) if update.from_id else None
        else:
            self.log.warning(
                f"Unexpected message type in User#get_message_details: {type(update)}")
            return update, None, None
        return update, sender, portal

    @staticmethod
    async def _try_redact(portal: po.Portal, message: DBMessage) -> None:
        if not portal:
            return
        try:
            await portal.main_intent.redact(message.mx_room, message.mxid)
        except MatrixRequestError:
            pass

    async def delete_message(self, update: UpdateDeleteMessages) -> None:
        if len(update.messages) > MAX_DELETIONS:
            return

        for message in update.messages:
            message = DBMessage.get_by_tgid(TelegramID(message), self.tgid)
            if not message:
                continue
            message.delete()
            number_left = DBMessage.count_spaces_by_mxid(message.mxid, message.mx_room)
            if number_left == 0:
                portal = po.Portal.get_by_mxid(message.mx_room)
                await self._try_redact(portal, message)
        self.db.commit()

    async def delete_channel_message(self, update: UpdateDeleteChannelMessages) -> None:
        if len(update.messages) > MAX_DELETIONS:
            return

        portal = po.Portal.get_by_tgid(TelegramID(update.channel_id))
        if not portal:
            return

        for message in update.messages:
            message = DBMessage.get_by_tgid(TelegramID(message), portal.tgid)
            if not message:
                continue
            message.delete()
            await self._try_redact(portal, message)
        self.db.commit()

    async def update_message(self, original_update: UpdateMessage) -> None:
        update, sender, portal = self.get_message_details(original_update)

        if self.is_bot and not portal.mxid:
            self.log.debug(f"Ignoring message received by bot in unbridged chat %s",
                           portal.tgid_log)
            return

        if self.ignore_incoming_bot_events and self.bot and sender.id == self.bot.tgid:
            self.log.debug(f"Ignoring relaybot-sent message %s to %s", update, portal.tgid_log)
            return

        if isinstance(update, MessageService):
            if isinstance(update.action, MessageActionChannelMigrateFrom):
                self.log.debug(f"Ignoring action %s to %s by %d", update.action,
                               portal.tgid_log,
                               sender.id)
                return
            self.log.debug("Handling action %s to %s by %d", update.action, portal.tgid_log,
                           sender.id)
            return await portal.handle_telegram_action(self, sender, update)

        user = sender.tgid if sender else "admin"
        if isinstance(original_update, (UpdateEditMessage, UpdateEditChannelMessage)):
            if config["bridge.edits_as_replies"]:
                self.log.debug("Handling edit %s to %s by %s", update, portal.tgid_log, user)
                return await portal.handle_telegram_edit(self, sender, update)
            return

        self.log.debug("Handling message %s to %s by %s", update, portal.tgid_log, user)
        return await portal.handle_telegram_message(self, sender, update)

    # endregion


def init(context: "Context") -> None:
    global config, MAX_DELETIONS
    AbstractUser.az, AbstractUser.db, config, AbstractUser.loop, AbstractUser.relaybot = context.core
    AbstractUser.ignore_incoming_bot_events = config["bridge.relaybot.ignore_own_incoming_events"]
    AbstractUser.session_container = context.session_container
    MAX_DELETIONS = config.get("bridge.max_telegram_delete", 10)
