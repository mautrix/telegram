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

from typing import TYPE_CHECKING, Any, Union
from abc import ABC, abstractmethod
import asyncio
import logging
import platform
import time

from telethon.errors import UnauthorizedError
from telethon.network import (
    Connection,
    ConnectionTcpFull,
    ConnectionTcpMTProxyRandomizedIntermediate,
)
from telethon.sessions import Session
from telethon.tl.patched import Message, MessageService
from telethon.tl.types import (
    Channel,
    Chat,
    MessageActionChannelMigrateFrom,
    MessageEmpty,
    PeerChannel,
    PeerChat,
    PeerUser,
    TypeUpdate,
    UpdateChannel,
    UpdateChannelUserTyping,
    UpdateChatParticipantAdmin,
    UpdateChatParticipants,
    UpdateChatUserTyping,
    UpdateDeleteChannelMessages,
    UpdateDeleteMessages,
    UpdateEditChannelMessage,
    UpdateEditMessage,
    UpdateFolderPeers,
    UpdateMessageReactions,
    UpdateNewChannelMessage,
    UpdateNewMessage,
    UpdateNotifySettings,
    UpdatePinnedChannelMessages,
    UpdatePinnedDialogs,
    UpdatePinnedMessages,
    UpdateReadChannelInbox,
    UpdateReadHistoryInbox,
    UpdateReadHistoryOutbox,
    UpdateShort,
    UpdateShortChatMessage,
    UpdateShortMessage,
    UpdateUserName,
    UpdateUserPhoto,
    UpdateUserStatus,
    UpdateUserTyping,
    User,
    UserStatusOffline,
    UserStatusOnline,
)

from mautrix.appservice import AppService
from mautrix.errors import MatrixError
from mautrix.types import PresenceState, UserID
from mautrix.util.logging import TraceLogger
from mautrix.util.opt_prometheus import Counter, Histogram

from . import __version__, portal as po, puppet as pu
from .config import Config
from .db import Message as DBMessage, PgSession
from .tgclient import MautrixTelegramClient
from .types import TelegramID

if TYPE_CHECKING:
    from .__main__ import TelegramBridge
    from .bot import Bot

UpdateMessage = Union[
    UpdateShortChatMessage,
    UpdateShortMessage,
    UpdateNewChannelMessage,
    UpdateNewMessage,
    UpdateEditMessage,
    UpdateEditChannelMessage,
]
UpdateMessageContent = Union[
    UpdateShortMessage, UpdateShortChatMessage, Message, MessageService, MessageEmpty
]

UPDATE_TIME = Histogram(
    name="bridge_telegram_update",
    documentation="Time spent processing Telegram updates",
    labelnames=("update_type",),
)
UPDATE_ERRORS = Counter(
    name="bridge_telegram_update_error",
    documentation="Number of fatal errors while handling Telegram updates",
    labelnames=("update_type",),
)


class AbstractUser(ABC):
    loop: asyncio.AbstractEventLoop = None
    log: TraceLogger
    az: AppService
    bridge: "TelegramBridge"
    config: Config
    relaybot: "Bot"
    ignore_incoming_bot_events: bool = True
    max_deletions: int = 10

    client: MautrixTelegramClient | None
    mxid: UserID | None

    tgid: TelegramID | None
    username: str | None
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
    def _proxy_settings(self) -> tuple[type[Connection], tuple[Any, ...] | None]:
        proxy_type = self.config["telegram.proxy.type"].lower()
        connection = ConnectionTcpFull
        connection_data = (
            self.config["telegram.proxy.address"],
            self.config["telegram.proxy.port"],
            self.config["telegram.proxy.rdns"],
            self.config["telegram.proxy.username"],
            self.config["telegram.proxy.password"],
        )
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

    @classmethod
    def init_cls(cls, bridge: "TelegramBridge") -> None:
        cls.bridge = bridge
        cls.config = bridge.config
        cls.loop = bridge.loop
        cls.az = bridge.az
        cls.ignore_incoming_bot_events = cls.config["bridge.relaybot.ignore_own_incoming_events"]
        cls.max_deletions = cls.config["bridge.max_telegram_delete"]

    async def _init_client(self) -> None:
        self.log.debug(f"Initializing client for {self.name}")

        session = await PgSession.get(self.name)
        if self.config["telegram.server.enabled"]:
            session.set_dc(
                self.config["telegram.server.dc"],
                self.config["telegram.server.ip"],
                self.config["telegram.server.port"],
            )

        if self.is_relaybot:
            base_logger = logging.getLogger("telethon.relaybot")
        else:
            base_logger = logging.getLogger(f"telethon.{self.tgid or -hash(self.mxid)}")

        device = self.config["telegram.device_info.device_model"]
        sysversion = self.config["telegram.device_info.system_version"]
        appversion = self.config["telegram.device_info.app_version"]
        connection, proxy = self._proxy_settings

        assert isinstance(session, Session)

        self.client = MautrixTelegramClient(
            session=session,
            api_id=self.config["telegram.api_id"],
            api_hash=self.config["telegram.api_hash"],
            app_version=__version__ if appversion == "auto" else appversion,
            system_version=(
                MautrixTelegramClient.__version__ if sysversion == "auto" else sysversion
            ),
            device_model=(
                f"{platform.system()} {platform.release()}" if device == "auto" else device
            ),
            timeout=self.config["telegram.connection.timeout"],
            connection_retries=self.config["telegram.connection.retries"],
            retry_delay=self.config["telegram.connection.retry_delay"],
            flood_sleep_threshold=self.config["telegram.connection.flood_sleep_threshold"],
            request_retries=self.config["telegram.connection.request_retries"],
            connection=connection,
            proxy=proxy,
            raise_last_call_error=True,
            catch_up=self.config["telegram.catch_up"],
            sequential_updates=self.config["telegram.sequential_updates"],
            loop=self.loop,
            base_logger=base_logger,
            update_error_callback=self._telethon_update_error_callback,
        )
        self.client.add_event_handler(self._update_catch)

    async def _telethon_update_error_callback(self, err: Exception) -> None:
        if self.config["telegram.exit_on_update_error"]:
            self.log.critical(f"Stopping due to update handling error {type(err).__name__}")
            self.bridge.manual_stop(50)
        else:
            if isinstance(err, UnauthorizedError):
                self.log.warning("Not recreating Telethon update loop")
                return
            self.log.info("Recreating Telethon update loop in 60 seconds")
            await asyncio.sleep(60)
            self.log.debug("Now recreating Telethon update loop")
            self.client._updates_handle = self.loop.create_task(self.client._update_loop())

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
            self.log.exception("Failed to handle Telegram update")
            UPDATE_ERRORS.labels(update_type=update_type).inc()
        UPDATE_TIME.labels(update_type=update_type).observe(time.time() - start_time)

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError()

    async def is_logged_in(self) -> bool:
        return (
            self.client and self.client.is_connected() and await self.client.is_user_authorized()
        )

    async def has_full_access(self, allow_bot: bool = False) -> bool:
        return (
            self.puppet_whitelisted
            and (not self.is_bot or allow_bot)
            and await self.is_logged_in()
        )

    async def start(self, delete_unless_authenticated: bool = False) -> AbstractUser:
        if not self.client:
            await self._init_client()
        await self.client.connect()
        self.log.debug(f"{'Bot' if self.is_relaybot else self.mxid} connected: {self.connected}")
        return self

    async def ensure_started(self, even_if_no_session=False) -> AbstractUser:
        if self.connected:
            return self
        session_exists = await PgSession.has(self.mxid)
        if even_if_no_session or session_exists:
            self.log.debug(
                f"Starting client due to ensure_started({even_if_no_session=}, {session_exists=})"
            )
            await self.start(delete_unless_authenticated=not even_if_no_session)
        return self

    async def stop(self) -> None:
        if self.client:
            await self.client.disconnect()
            self.client = None

    # region Telegram update handling

    async def _update(self, update: TypeUpdate) -> None:
        if isinstance(update, UpdateShort):
            update = update.update
        asyncio.create_task(self._handle_entity_updates(getattr(update, "_entities", {})))
        if isinstance(
            update,
            (
                UpdateShortChatMessage,
                UpdateShortMessage,
                UpdateNewChannelMessage,
                UpdateNewMessage,
                UpdateEditMessage,
                UpdateEditChannelMessage,
            ),
        ):
            await self.update_message(update)
        elif isinstance(update, UpdateDeleteMessages):
            await self.delete_message(update)
        elif isinstance(update, UpdateDeleteChannelMessages):
            await self.delete_channel_message(update)
        elif isinstance(update, UpdateMessageReactions):
            await self.update_reactions(update)
        elif isinstance(update, (UpdateChatUserTyping, UpdateChannelUserTyping, UpdateUserTyping)):
            await self.update_typing(update)
        elif isinstance(update, UpdateUserStatus):
            await self.update_status(update)
        elif isinstance(update, UpdateChatParticipantAdmin):
            await self.update_admin(update)
        elif isinstance(update, UpdateChatParticipants):
            await self.update_participants(update)
        elif isinstance(update, (UpdatePinnedMessages, UpdatePinnedChannelMessages)):
            await self.update_pinned_messages(update)
        elif isinstance(update, (UpdateUserName, UpdateUserPhoto)):
            await self.update_others_info(update)
        elif isinstance(update, UpdateReadHistoryOutbox):
            await self.update_read_receipt(update)
        elif isinstance(update, (UpdateReadHistoryInbox, UpdateReadChannelInbox)):
            await self.update_own_read_receipt(update)
        elif isinstance(update, UpdateFolderPeers):
            await self.update_folder_peers(update)
        elif isinstance(update, UpdatePinnedDialogs):
            await self.update_pinned_dialogs(update)
        elif isinstance(update, UpdateNotifySettings):
            await self.update_notify_settings(update)
        elif isinstance(update, UpdateChannel):
            await self.update_channel(update)
        else:
            self.log.trace("Unhandled update: %s", update)

    async def update_folder_peers(self, update: UpdateFolderPeers) -> None:
        pass

    async def update_pinned_dialogs(self, update: UpdatePinnedDialogs) -> None:
        pass

    async def update_notify_settings(self, update: UpdateNotifySettings) -> None:
        pass

    async def update_pinned_messages(
        self, update: UpdatePinnedMessages | UpdatePinnedChannelMessages
    ) -> None:
        if isinstance(update, UpdatePinnedMessages):
            portal = await po.Portal.get_by_entity(update.peer, tg_receiver=self.tgid)
        else:
            portal = await po.Portal.get_by_tgid(TelegramID(update.channel_id))
        if portal and portal.mxid:
            await portal.receive_telegram_pin_ids(
                update.messages, self.tgid, remove=not update.pinned
            )

    @staticmethod
    async def update_participants(update: UpdateChatParticipants) -> None:
        portal = await po.Portal.get_by_tgid(TelegramID(update.participants.chat_id))
        if portal and portal.mxid:
            await portal.update_power_levels(update.participants.participants)

    async def update_read_receipt(self, update: UpdateReadHistoryOutbox) -> None:
        if not isinstance(update.peer, PeerUser):
            self.log.debug("Unexpected read receipt peer: %s", update.peer)
            return

        portal = await po.Portal.get_by_tgid(
            TelegramID(update.peer.user_id), tg_receiver=self.tgid
        )
        if not portal or not portal.mxid:
            return

        # We check that these are user read receipts, so tg_space is always the user ID.
        message = await DBMessage.get_one_by_tgid(
            TelegramID(update.max_id), self.tgid, edit_index=-1
        )
        if not message:
            return

        puppet = await pu.Puppet.get_by_peer(update.peer)
        await puppet.intent.mark_read(portal.mxid, message.mxid)

    async def update_own_read_receipt(
        self, update: UpdateReadHistoryInbox | UpdateReadChannelInbox
    ) -> None:
        puppet = await pu.Puppet.get_by_tgid(self.tgid)
        if not puppet.is_real_user:
            return

        if isinstance(update, UpdateReadChannelInbox):
            portal = await po.Portal.get_by_tgid(TelegramID(update.channel_id))
        elif isinstance(update.peer, PeerChat):
            portal = await po.Portal.get_by_tgid(TelegramID(update.peer.chat_id))
        elif isinstance(update.peer, PeerUser):
            portal = await po.Portal.get_by_tgid(
                TelegramID(update.peer.user_id), tg_receiver=self.tgid
            )
        else:
            self.log.debug("Unexpected own read receipt peer: %s", update.peer)
            return

        if not portal or not portal.mxid:
            return

        tg_space = portal.tgid if portal.peer_type == "channel" else self.tgid
        message = await DBMessage.get_one_by_tgid(
            TelegramID(update.max_id), tg_space, edit_index=-1
        )
        if not message:
            return

        await puppet.intent.mark_read(portal.mxid, message.mxid)

    async def update_admin(self, update: UpdateChatParticipantAdmin) -> None:
        # TODO duplication not checked
        portal = await po.Portal.get_by_tgid(TelegramID(update.chat_id))
        if not portal or not portal.mxid:
            return

        await portal.set_telegram_admin(TelegramID(update.user_id))

    async def update_typing(
        self, update: UpdateUserTyping | UpdateChatUserTyping | UpdateChannelUserTyping
    ) -> None:
        sender = None
        if isinstance(update, UpdateUserTyping):
            portal = await po.Portal.get_by_tgid(
                TelegramID(update.user_id), tg_receiver=self.tgid, peer_type="user"
            )
            sender = await pu.Puppet.get_by_tgid(TelegramID(update.user_id))
        elif isinstance(update, UpdateChannelUserTyping):
            portal = await po.Portal.get_by_tgid(TelegramID(update.channel_id))
        elif isinstance(update, UpdateChatUserTyping):
            portal = await po.Portal.get_by_tgid(TelegramID(update.chat_id))
        else:
            return

        if isinstance(update, (UpdateChannelUserTyping, UpdateChatUserTyping)):
            sender = await pu.Puppet.get_by_peer(update.from_id)

        if not sender or not portal or not portal.mxid:
            return

        await portal.handle_telegram_typing(sender, update)

    async def _handle_entity_updates(self, entities: dict[int, User | Chat | Channel]) -> None:
        try:
            users = (entity for entity in entities.values() if isinstance(entity, (User, Channel)))
            puppets = ((await pu.Puppet.get_by_peer(user), user) for user in users)
            await asyncio.gather(
                *[puppet.try_update_info(self, info) async for puppet, info in puppets if puppet]
            )
        except Exception:
            self.log.exception("Failed to handle entity updates")

    async def update_others_info(self, update: UpdateUserName | UpdateUserPhoto) -> None:
        # TODO duplication not checked
        puppet = await pu.Puppet.get_by_tgid(TelegramID(update.user_id))
        if isinstance(update, UpdateUserName):
            puppet.username = update.username
            if await puppet.update_displayname(self, update):
                await puppet.save()
                await puppet.update_portals_meta()
        elif isinstance(update, UpdateUserPhoto):
            if await puppet.update_avatar(self, update.photo):
                await puppet.save()
                await puppet.update_portals_meta()
        else:
            self.log.warning(f"Unexpected other user info update: {type(update)}")

    async def update_status(self, update: UpdateUserStatus) -> None:
        puppet = await pu.Puppet.get_by_tgid(TelegramID(update.user_id))
        if isinstance(update.status, UserStatusOnline):
            await puppet.default_mxid_intent.set_presence(PresenceState.ONLINE)
        elif isinstance(update.status, UserStatusOffline):
            await puppet.default_mxid_intent.set_presence(PresenceState.OFFLINE)
        else:
            self.log.warning(f"Unexpected user status update: type({update})")
        return

    async def get_message_details(
        self, update: UpdateMessage
    ) -> tuple[UpdateMessageContent, pu.Puppet | None, po.Portal | None]:
        if isinstance(update, UpdateShortChatMessage):
            portal = await po.Portal.get_by_tgid(TelegramID(update.chat_id))
            if not portal:
                self.log.warning(f"Received message in chat with unknown type {update.chat_id}")
            sender = await pu.Puppet.get_by_tgid(TelegramID(update.from_id))
        elif isinstance(update, UpdateShortMessage):
            portal = await po.Portal.get_by_tgid(
                TelegramID(update.user_id), tg_receiver=self.tgid, peer_type="user"
            )
            sender = await pu.Puppet.get_by_tgid(self.tgid if update.out else update.user_id)
        elif isinstance(
            update,
            (
                UpdateNewMessage,
                UpdateNewChannelMessage,
                UpdateEditMessage,
                UpdateEditChannelMessage,
            ),
        ):
            update = update.message
            if isinstance(update, MessageEmpty):
                return update, None, None
            portal = await po.Portal.get_by_entity(update.peer_id, tg_receiver=self.tgid)
            if update.out:
                sender = await pu.Puppet.get_by_tgid(self.tgid)
            elif isinstance(update.from_id, (PeerUser, PeerChannel)):
                sender = await pu.Puppet.get_by_peer(update.from_id)
            else:
                sender = None
        else:
            self.log.warning(
                f"Unexpected message type in User#get_message_details: {type(update)}"
            )
            return update, None, None
        return update, sender, portal

    @staticmethod
    async def _try_redact(message: DBMessage) -> None:
        portal = await po.Portal.get_by_mxid(message.mx_room)
        if not portal:
            return
        try:
            await portal.main_intent.redact(message.mx_room, message.mxid)
        except MatrixError:
            pass

    async def delete_message(self, update: UpdateDeleteMessages) -> None:
        if len(update.messages) > self.max_deletions:
            return

        for message_id in update.messages:
            for message in await DBMessage.get_all_by_tgid(TelegramID(message_id), self.tgid):
                if message.redacted:
                    continue
                await message.delete()
                number_left = await DBMessage.count_spaces_by_mxid(message.mxid, message.mx_room)
                if number_left == 0:
                    await self._try_redact(message)

    async def delete_channel_message(self, update: UpdateDeleteChannelMessages) -> None:
        if len(update.messages) > self.max_deletions:
            return

        channel_id = TelegramID(update.channel_id)

        for message_id in update.messages:
            for message in await DBMessage.get_all_by_tgid(TelegramID(message_id), channel_id):
                if message.redacted:
                    continue
                await message.delete()
                await self._try_redact(message)

    async def update_reactions(self, update: UpdateMessageReactions) -> None:
        portal = await po.Portal.get_by_entity(update.peer, tg_receiver=self.tgid)
        if not portal or not portal.mxid or not portal.allow_bridging:
            return
        await portal.handle_telegram_reactions(self, TelegramID(update.msg_id), update.reactions)

    async def update_channel(self, update: UpdateChannel) -> None:
        portal = await po.Portal.get_by_tgid(TelegramID(update.channel_id))
        if not portal:
            return
        if getattr(update, "mau_telethon_is_leave", False):
            self.log.debug("UpdateChannel has mau_telethon_is_leave, leaving portal")
            await portal.delete_telegram_user(self.tgid, sender=None)
        elif chan := getattr(update, "mau_channel", None):
            if not portal.mxid:
                asyncio.create_task(self._delayed_create_channel(chan))
            else:
                self.log.debug("Updating channel info with data fetched by Telethon")
                await portal.update_info(self, chan)
                await portal.invite_to_matrix(self.mxid)

    async def _delayed_create_channel(self, chan: Channel) -> None:
        self.log.debug("Waiting 5 seconds before handling UpdateChannel for non-existent portal")
        await asyncio.sleep(5)
        portal = await po.Portal.get_by_tgid(TelegramID(chan.id))
        if portal.mxid:
            self.log.debug(
                "Portal started existing after waiting 5 seconds, dropping UpdateChannel"
            )
            return
        else:
            self.log.info(
                "Creating Matrix room with data fetched by Telethon due to UpdateChannel"
            )
            await portal.create_matrix_room(self, chan)

    async def update_message(self, original_update: UpdateMessage) -> None:
        update, sender, portal = await self.get_message_details(original_update)
        if not portal:
            return
        elif portal and not portal.allow_bridging:
            self.log.debug(f"Ignoring message in portal {portal.tgid_log} (bridging disallowed)")
            return

        if self.is_relaybot:
            if update.is_private:
                if not self.config["bridge.relaybot.private_chat.invite"]:
                    if sender:
                        self.log.debug(f"Ignoring private message to bot from {sender.id}")
                    return
            elif not portal.mxid and self.config["bridge.relaybot.ignore_unbridged_group_chat"]:
                self.log.debug(
                    f"Ignoring message received by bot in unbridged chat {portal.tgid_log}"
                )
                return

        if (
            self.ignore_incoming_bot_events
            and self.relaybot
            and sender
            and sender.id == self.relaybot.tgid
        ):
            self.log.debug("Ignoring relaybot-sent message %s to %s", update.id, portal.tgid_log)
            return

        await portal.backfill_lock.wait(f"update {update.id}")

        if isinstance(update, MessageService):
            if isinstance(update.action, MessageActionChannelMigrateFrom):
                self.log.trace(
                    "Received %s in %s by %d, unregistering portal...",
                    update.action,
                    portal.tgid_log,
                    sender.id,
                )
                await self.unregister_portal(update.action.chat_id, update.action.chat_id)
                await self.register_portal(portal)
                return
            self.log.trace(
                "Handling action %s to %s by %d",
                update.action,
                portal.tgid_log,
                (sender.id if sender else 0),
            )
            return await portal.handle_telegram_action(self, sender, update)

        if isinstance(original_update, (UpdateEditMessage, UpdateEditChannelMessage)):
            return await portal.handle_telegram_edit(self, sender, update)
        return await portal.handle_telegram_message(self, sender, update)

    # endregion
