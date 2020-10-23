# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2020 Tulir Asokan
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
from typing import Tuple, Optional, Union, Dict, Type, Any, TYPE_CHECKING
from abc import ABC, abstractmethod
import asyncio
import logging
import platform
import time

from telethon.sessions import Session
from telethon.network import (ConnectionTcpMTProxyRandomizedIntermediate, ConnectionTcpFull,
                              Connection)
from telethon.tl.patched import MessageService, Message
from telethon.tl.types import (
    Channel, Chat, MessageActionChannelMigrateFrom, PeerUser, TypeUpdate, UpdateChatPinnedMessage,
    UpdateChannelPinnedMessage, UpdateChatParticipantAdmin, UpdateChatParticipants, PeerChat,
    UpdateChatUserTyping, UpdateDeleteChannelMessages, UpdateNewMessage, UpdateDeleteMessages,
    UpdateEditChannelMessage, UpdateEditMessage, UpdateNewChannelMessage, UpdateReadHistoryOutbox,
    UpdateShortChatMessage, UpdateShortMessage, UpdateUserName, UpdateUserPhoto, UpdateUserStatus,
    UpdateUserTyping, User, UserStatusOffline, UserStatusOnline, UpdateReadHistoryInbox,
    UpdateReadChannelInbox, MessageEmpty)

from mautrix.types import UserID, PresenceState
from mautrix.errors import MatrixError
from mautrix.appservice import AppService
from mautrix.util.logging import TraceLogger
from mautrix.util.opt_prometheus import Histogram, Counter
from alchemysession import AlchemySessionContainer

from . import portal as po, puppet as pu, __version__
from .db import Message as DBMessage
from .types import TelegramID
from .tgclient import MautrixTelegramClient

if TYPE_CHECKING:
    from .context import Context
    from .config import Config
    from .bot import Bot

config: Optional['Config'] = None
# Value updated from config in init()
MAX_DELETIONS: int = 10

UpdateMessage = Union[UpdateShortChatMessage, UpdateShortMessage, UpdateNewChannelMessage,
                      UpdateNewMessage, UpdateEditMessage, UpdateEditChannelMessage]
UpdateMessageContent = Union[UpdateShortMessage, UpdateShortChatMessage, Message, MessageService]

UPDATE_TIME = Histogram("bridge_telegram_update", "Time spent processing Telegram updates",
                        ("update_type",))
UPDATE_ERRORS = Counter("bridge_telegram_update_error",
                        "Number of fatal errors while handling Telegram updates", ("update_type",))


class AbstractUser(ABC):
    session_container: AlchemySessionContainer = None
    loop: asyncio.AbstractEventLoop = None
    log: TraceLogger
    az: AppService
    relaybot: Optional['Bot']
    ignore_incoming_bot_events: bool = True

    client: Optional[MautrixTelegramClient]
    mxid: Optional[UserID]

    tgid: Optional[TelegramID]
    username: Optional['str']
    is_bot: bool

    is_relaybot: bool

    puppet_whitelisted: bool
    whitelisted: bool
    relaybot_whitelisted: bool
    matrix_puppet_whitelisted: bool
    is_admin: bool

    def __init__(self) -> None:
        self.is_admin = False
        self.matrix_puppet_whitelisted = False
        self.puppet_whitelisted = False
        self.whitelisted = False
        self.relaybot_whitelisted = False
        self.client = None
        self.is_relaybot = False
        self.is_bot = False

    @property
    def connected(self) -> bool:
        return self.client and self.client.is_connected()

    @property
    def _proxy_settings(self) -> Tuple[Type[Connection], Optional[Tuple[Any, ...]]]:
        proxy_type = config["telegram.proxy.type"].lower()
        connection = ConnectionTcpFull
        connection_data = (config["telegram.proxy.address"],
                           config["telegram.proxy.port"],
                           config["telegram.proxy.rdns"],
                           config["telegram.proxy.username"],
                           config["telegram.proxy.password"])
        if proxy_type == "disabled":
            connection_data = None
        elif proxy_type == "socks4":
            connection_data = (1,) + connection_data
        elif proxy_type == "socks5":
            connection_data = (2,) + connection_data
        elif proxy_type == "http":
            connection_data = (3,) + connection_data
        elif proxy_type == "mtproxy":
            connection = ConnectionTcpMTProxyRandomizedIntermediate
            connection_data = (connection_data[0], connection_data[1], connection_data[4])

        return connection, connection_data

    def _init_client(self) -> None:
        self.log.debug(f"Initializing client for {self.name}")

        self.session = self.session_container.new_session(self.name)
        if config["telegram.server.enabled"]:
            self.session.set_dc(config["telegram.server.dc"],
                                config["telegram.server.ip"],
                                config["telegram.server.port"])

        if self.is_relaybot:
            base_logger = logging.getLogger("telethon.relaybot")
        else:
            base_logger = logging.getLogger(f"telethon.{self.tgid or -hash(self.mxid)}")

        device = config["telegram.device_info.device_model"]
        sysversion = config["telegram.device_info.system_version"]
        appversion = config["telegram.device_info.app_version"]
        connection, proxy = self._proxy_settings

        assert isinstance(self.session, Session)

        self.client = MautrixTelegramClient(
            session=self.session,

            api_id=config["telegram.api_id"],
            api_hash=config["telegram.api_hash"],

            app_version=__version__ if appversion == "auto" else appversion,
            system_version=(MautrixTelegramClient.__version__
                            if sysversion == "auto" else sysversion),
            device_model=(f"{platform.system()} {platform.release()}"
                          if device == "auto" else device),

            timeout=config["telegram.connection.timeout"],
            connection_retries=config["telegram.connection.retries"],
            retry_delay=config["telegram.connection.retry_delay"],
            flood_sleep_threshold=config["telegram.connection.flood_sleep_threshold"],
            request_retries=config["telegram.connection.request_retries"],
            connection=connection,
            proxy=proxy,
            raise_last_call_error=True,

            loop=self.loop,
            base_logger=base_logger
        )
        self.client.add_event_handler(self._update_catch)

    @abstractmethod
    async def update(self, update: TypeUpdate) -> bool:
        return False

    @abstractmethod
    async def post_login(self) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def register_portal(self, portal: po.Portal) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def unregister_portal(self, tgid: int, tg_receiver: int) -> None:
        raise NotImplementedError()

    async def _update_catch(self, update: TypeUpdate) -> None:
        start_time = time.time()
        update_type = type(update).__name__
        try:
            if not await self.update(update):
                await self._update(update)
        except Exception:
            self.log.exception(f"Failed to handle Telegram update {update}")
            UPDATE_ERRORS.labels(update_type=update_type).inc()
        UPDATE_TIME.labels(update_type=update_type).observe(time.time() - start_time)

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError()

    async def is_logged_in(self) -> bool:
        return (self.client and self.client.is_connected()
                and await self.client.is_user_authorized())

    async def has_full_access(self, allow_bot: bool = False) -> bool:
        return (self.puppet_whitelisted
                and (not self.is_bot or allow_bot)
                and await self.is_logged_in())

    async def start(self, delete_unless_authenticated: bool = False) -> 'AbstractUser':
        if not self.client:
            self._init_client()
        await self.client.connect()
        self.log.debug(f"{'Bot' if self.is_relaybot else self.mxid} connected: {self.connected}")
        return self

    async def ensure_started(self, even_if_no_session=False) -> 'AbstractUser':
        if self.connected:
            return self
        if even_if_no_session or self.session_container.has_session(self.mxid):
            self.log.debug("Starting client due to ensure_started"
                           f"(even_if_no_session={even_if_no_session})")
            await self.start(delete_unless_authenticated=not even_if_no_session)
        return self

    async def stop(self) -> None:
        await self.client.disconnect()
        self.client = None

    # region Telegram update handling

    async def _update(self, update: TypeUpdate) -> None:
        asyncio.ensure_future(self._handle_entity_updates(getattr(update, "_entities", {})),
                              loop=self.loop)
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
        elif isinstance(update, UpdateChatParticipantAdmin):
            await self.update_admin(update)
        elif isinstance(update, UpdateChatParticipants):
            await self.update_participants(update)
        elif isinstance(update, (UpdateChannelPinnedMessage, UpdateChatPinnedMessage)):
            await self.update_pinned_messages(update)
        elif isinstance(update, (UpdateUserName, UpdateUserPhoto)):
            await self.update_others_info(update)
        elif isinstance(update, UpdateReadHistoryOutbox):
            await self.update_read_receipt(update)
        elif isinstance(update, (UpdateReadHistoryInbox, UpdateReadChannelInbox)):
            await self.update_own_read_receipt(update)
        else:
            self.log.trace("Unhandled update: %s", update)

    async def update_pinned_messages(self, update: Union[UpdateChannelPinnedMessage,
                                                         UpdateChatPinnedMessage]) -> None:
        if isinstance(update, UpdateChatPinnedMessage):
            portal = po.Portal.get_by_tgid(TelegramID(update.chat_id))
        else:
            portal = po.Portal.get_by_tgid(TelegramID(update.channel_id))
        if portal and portal.mxid:
            await portal.receive_telegram_pin_id(update.id, self.tgid)

    @staticmethod
    async def update_participants(update: UpdateChatParticipants) -> None:
        portal = po.Portal.get_by_tgid(TelegramID(update.participants.chat_id))
        if portal and portal.mxid:
            await portal.update_power_levels(update.participants.participants)

    async def update_read_receipt(self, update: UpdateReadHistoryOutbox) -> None:
        if not isinstance(update.peer, PeerUser):
            self.log.debug("Unexpected read receipt peer: %s", update.peer)
            return

        portal = po.Portal.get_by_tgid(TelegramID(update.peer.user_id), self.tgid)
        if not portal or not portal.mxid:
            return

        # We check that these are user read receipts, so tg_space is always the user ID.
        message = DBMessage.get_one_by_tgid(TelegramID(update.max_id), self.tgid, edit_index=-1)
        if not message:
            return

        puppet = pu.Puppet.get(TelegramID(update.peer.user_id))
        await puppet.intent.mark_read(portal.mxid, message.mxid)

    async def update_own_read_receipt(self, update: Union[UpdateReadHistoryInbox,
                                                          UpdateReadChannelInbox]) -> None:
        puppet = pu.Puppet.get(self.tgid)
        if not puppet.is_real_user:
            return

        if isinstance(update, UpdateReadChannelInbox):
            portal = po.Portal.get_by_tgid(TelegramID(update.channel_id))
        elif isinstance(update.peer, PeerChat):
            portal = po.Portal.get_by_tgid(TelegramID(update.peer.chat_id))
        elif isinstance(update.peer, PeerUser):
            portal = po.Portal.get_by_tgid(TelegramID(update.peer.user_id), self.tgid)
        else:
            self.log.debug("Unexpected own read receipt peer: %s", update.peer)
            return

        if not portal or not portal.mxid:
            return

        tg_space = portal.tgid if portal.peer_type == "channel" else self.tgid
        message = DBMessage.get_one_by_tgid(TelegramID(update.max_id), tg_space, edit_index=-1)
        if not message:
            return

        await puppet.intent.mark_read(portal.mxid, message.mxid)

    async def update_admin(self, update: UpdateChatParticipantAdmin) -> None:
        # TODO duplication not checked
        portal = po.Portal.get_by_tgid(TelegramID(update.chat_id))
        if not portal or not portal.mxid:
            return

        await portal.set_telegram_admin(TelegramID(update.user_id))

    async def update_typing(self, update: Union[UpdateUserTyping, UpdateChatUserTyping]) -> None:
        if isinstance(update, UpdateUserTyping):
            portal = po.Portal.get_by_tgid(TelegramID(update.user_id), self.tgid, "user")
        else:
            portal = po.Portal.get_by_tgid(TelegramID(update.chat_id))

        if not portal or not portal.mxid:
            return

        sender = pu.Puppet.get(TelegramID(update.user_id))
        await portal.handle_telegram_typing(sender, update)

    async def _handle_entity_updates(self, entities: Dict[int, Union[User, Chat, Channel]]
                                     ) -> None:
        try:
            users = (entity for entity in entities.values() if isinstance(entity, User))
            puppets = ((pu.Puppet.get(TelegramID(user.id)), user) for user in users)
            await asyncio.gather(*[puppet.try_update_info(self, info)
                                   for puppet, info in puppets if puppet])
        except Exception:
            self.log.exception("Failed to handle entity updates")

    async def update_others_info(self, update: Union[UpdateUserName, UpdateUserPhoto]) -> None:
        # TODO duplication not checked
        puppet = pu.Puppet.get(TelegramID(update.user_id))
        if isinstance(update, UpdateUserName):
            puppet.username = update.username
            if await puppet.update_displayname(self, update):
                await puppet.save()
        elif isinstance(update, UpdateUserPhoto):
            if await puppet.update_avatar(self, update.photo):
                await puppet.save()
        else:
            self.log.warning(f"Unexpected other user info update: {type(update)}")

    async def update_status(self, update: UpdateUserStatus) -> None:
        puppet = pu.Puppet.get(TelegramID(update.user_id))
        if isinstance(update.status, UserStatusOnline):
            await puppet.default_mxid_intent.set_presence(PresenceState.ONLINE)
        elif isinstance(update.status, UserStatusOffline):
            await puppet.default_mxid_intent.set_presence(PresenceState.OFFLINE)
        else:
            self.log.warning(f"Unexpected user status update: type({update})")
        return

    def get_message_details(self, update: UpdateMessage) -> Tuple[UpdateMessageContent,
                                                                  Optional[pu.Puppet],
                                                                  Optional[po.Portal]]:
        if isinstance(update, UpdateShortChatMessage):
            portal = po.Portal.get_by_tgid(TelegramID(update.chat_id))
            if not portal:
                self.log.warning(f"Received message in chat with unknown type {update.chat_id}")
            sender = pu.Puppet.get(TelegramID(update.from_id))
        elif isinstance(update, UpdateShortMessage):
            portal = po.Portal.get_by_tgid(TelegramID(update.user_id), self.tgid, "user")
            sender = pu.Puppet.get(self.tgid if update.out else update.user_id)
        elif isinstance(update, (UpdateNewMessage, UpdateNewChannelMessage,
                                 UpdateEditMessage, UpdateEditChannelMessage)):
            update = update.message
            if isinstance(update, MessageEmpty):
                return update, None, None
            portal = po.Portal.get_by_entity(update.peer_id, receiver_id=self.tgid)
            if update.out:
                sender = pu.Puppet.get(self.tgid)
            elif isinstance(update.from_id, PeerUser):
                sender = pu.Puppet.get(TelegramID(update.from_id.user_id))
            else:
                sender = None
        else:
            self.log.warning("Unexpected message type in User#get_message_details: "
                             f"{type(update)}")
            return update, None, None
        return update, sender, portal

    @staticmethod
    async def _try_redact(message: DBMessage) -> None:
        portal = po.Portal.get_by_mxid(message.mx_room)
        if not portal:
            return
        try:
            await portal.main_intent.redact(message.mx_room, message.mxid)
        except MatrixError:
            pass

    async def delete_message(self, update: UpdateDeleteMessages) -> None:
        if len(update.messages) > MAX_DELETIONS:
            return

        for message_id in update.messages:
            for message in DBMessage.get_all_by_tgid(TelegramID(message_id), self.tgid):
                message.delete()
                number_left = DBMessage.count_spaces_by_mxid(message.mxid, message.mx_room)
                if number_left == 0:
                    await self._try_redact(message)

    async def delete_channel_message(self, update: UpdateDeleteChannelMessages) -> None:
        if len(update.messages) > MAX_DELETIONS:
            return

        channel_id = TelegramID(update.channel_id)

        for message_id in update.messages:
            for message in DBMessage.get_all_by_tgid(TelegramID(message_id), channel_id):
                message.delete()
                await self._try_redact(message)

    async def update_message(self, original_update: UpdateMessage) -> None:
        update, sender, portal = self.get_message_details(original_update)
        if not portal:
            return
        elif portal and not portal.allow_bridging:
            self.log.debug(f"Ignoring message in portal {portal.tgid_log} (bridging disallowed)")
            return

        if self.is_relaybot:
            if update.is_private:
                if not config["bridge.relaybot.private_chat.invite"]:
                    self.log.debug(f"Ignoring private message to bot from {sender.id}")
                    return
            elif not portal.mxid and config["bridge.relaybot.ignore_unbridged_group_chat"]:
                self.log.debug("Ignoring message received by bot"
                               f" in unbridged chat {portal.tgid_log}")
                return

        if ((self.ignore_incoming_bot_events and self.relaybot
             and sender and sender.id == self.relaybot.tgid)):
            self.log.debug(f"Ignoring relaybot-sent message %s to %s", update.id, portal.tgid_log)
            return

        await portal.backfill_lock.wait(update.id)

        if isinstance(update, MessageService):
            if isinstance(update.action, MessageActionChannelMigrateFrom):
                self.log.trace(f"Received %s in %s by %d, unregistering portal...",
                               update.action, portal.tgid_log, sender.id)
                await self.unregister_portal(update.action.chat_id, update.action.chat_id)
                await self.register_portal(portal)
                return
            self.log.trace("Handling action %s to %s by %d", update.action, portal.tgid_log,
                           sender.id)
            return await portal.handle_telegram_action(self, sender, update)

        if isinstance(original_update, (UpdateEditMessage, UpdateEditChannelMessage)):
            return await portal.handle_telegram_edit(self, sender, update)
        return await portal.handle_telegram_message(self, sender, update)

    # endregion


def init(context: 'Context') -> None:
    global config, MAX_DELETIONS
    AbstractUser.az, config, AbstractUser.loop, AbstractUser.relaybot = context.core
    AbstractUser.ignore_incoming_bot_events = config["bridge.relaybot.ignore_own_incoming_events"]
    AbstractUser.session_container = context.session_container
    MAX_DELETIONS = config.get("bridge.max_telegram_delete", 10)
