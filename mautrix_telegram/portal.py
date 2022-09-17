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

from typing import TYPE_CHECKING, Any, AsyncGenerator, Awaitable, Callable, List, Union, cast
from collections import defaultdict
from datetime import datetime
from html import escape as escape_html
from sqlite3 import IntegrityError
from string import Template
import asyncio
import base64
import random
import time

from asyncpg import UniqueViolationError
from telethon.errors import (
    ChatNotModifiedError,
    MessageIdInvalidError,
    PhotoExtInvalidError,
    PhotoInvalidDimensionsError,
    PhotoSaveFileInvalidError,
    ReactionInvalidError,
    RPCError,
)
from telethon.tl.functions.channels import (
    CreateChannelRequest,
    EditPhotoRequest,
    EditTitleRequest,
    InviteToChannelRequest,
    JoinChannelRequest,
    UpdateUsernameRequest,
    ViewSponsoredMessageRequest,
)
from telethon.tl.functions.messages import (
    AddChatUserRequest,
    CreateChatRequest,
    EditChatAboutRequest,
    EditChatPhotoRequest,
    EditChatTitleRequest,
    ExportChatInviteRequest,
    GetMessageReactionsListRequest,
    GetMessagesReactionsRequest,
    MigrateChatRequest,
    SendReactionRequest,
    SetTypingRequest,
    UnpinAllMessagesRequest,
    UpdatePinnedMessageRequest,
)
from telethon.tl.patched import Message, MessageService
from telethon.tl.types import (
    Channel,
    ChannelFull,
    Chat,
    ChatFull,
    ChatPhoto,
    ChatPhotoEmpty,
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    DocumentAttributeImageSize,
    DocumentAttributeVideo,
    GeoPoint,
    InputChannel,
    InputChatUploadedPhoto,
    InputMediaUploadedDocument,
    InputMediaUploadedPhoto,
    InputPeerChannel,
    InputPeerChat,
    InputPeerPhotoFileLocation,
    InputPeerUser,
    InputUser,
    MessageActionChannelCreate,
    MessageActionChatAddUser,
    MessageActionChatCreate,
    MessageActionChatDeletePhoto,
    MessageActionChatDeleteUser,
    MessageActionChatEditPhoto,
    MessageActionChatEditTitle,
    MessageActionChatJoinedByLink,
    MessageActionChatJoinedByRequest,
    MessageActionChatMigrateTo,
    MessageActionContactSignUp,
    MessageActionGameScore,
    MessageMediaGame,
    MessageMediaGeo,
    MessagePeerReaction,
    MessageReactions,
    PeerChannel,
    PeerChat,
    PeerUser,
    Photo,
    PhotoEmpty,
    ReactionCount,
    ReactionCustomEmoji,
    ReactionEmoji,
    SendMessageCancelAction,
    SendMessageTypingAction,
    SponsoredMessage,
    TypeChannelParticipant,
    TypeChat,
    TypeChatParticipant,
    TypeInputChannel,
    TypeInputPeer,
    TypeMessage,
    TypeMessageAction,
    TypePeer,
    TypeReaction,
    TypeUser,
    TypeUserFull,
    TypeUserProfilePhoto,
    UpdateChannelUserTyping,
    UpdateChatUserTyping,
    UpdateMessageReactions,
    UpdateNewMessage,
    UpdateUserTyping,
    User,
    UserFull,
    UserProfilePhoto,
    UserProfilePhotoEmpty,
)
from telethon.utils import encode_waveform

from mautrix.appservice import DOUBLE_PUPPET_SOURCE_KEY, IntentAPI
from mautrix.bridge import BasePortal, NotificationDisabler, RejectMatrixInvite, async_getter_lock
from mautrix.errors import IntentError, MatrixRequestError, MForbidden
from mautrix.types import (
    BatchID,
    BeeperMessageStatusEventContent,
    ContentURI,
    EventID,
    EventType,
    Format,
    ImageInfo,
    JoinRule,
    LocationMessageEventContent,
    MediaMessageEventContent,
    Membership,
    MessageEventContent,
    MessageStatus,
    MessageStatusReason,
    MessageType,
    PowerLevelStateEventContent,
    RelatesTo,
    RelationType,
    RoomAlias,
    RoomAvatarStateEventContent,
    RoomCreatePreset,
    RoomID,
    RoomNameStateEventContent,
    RoomTopicStateEventContent,
    StateEventContent,
    TextMessageEventContent,
    UserID,
    VideoInfo,
)
from mautrix.util import magic, variation_selector
from mautrix.util.message_send_checkpoint import MessageSendCheckpointStatus
from mautrix.util.simple_lock import SimpleLock
from mautrix.util.simple_template import SimpleTemplate

from . import (
    abstract_user as au,
    formatter,
    matrix as m,
    portal_util as putil,
    puppet as p,
    user as u,
    util,
)
from .config import Config
from .db import (
    DisappearingMessage,
    Message as DBMessage,
    Portal as DBPortal,
    Reaction as DBReaction,
    TelegramFile as DBTelegramFile,
)
from .tgclient import MautrixTelegramClient
from .types import TelegramID
from .util import sane_mimetypes

try:
    from mautrix.crypto.attachments import decrypt_attachment
except ImportError:
    decrypt_attachment = None

if TYPE_CHECKING:
    from .__main__ import TelegramBridge
    from .bot import Bot

StateBridge = EventType.find("m.bridge", EventType.Class.STATE)
StateHalfShotBridge = EventType.find("uk.half-shot.bridge", EventType.Class.STATE)
DummyPortalCreated = EventType.find("fi.mau.dummy.portal_created", EventType.Class.MESSAGE)

InviteList = Union[UserID, List[UserID]]
UpdateTyping = Union[UpdateUserTyping, UpdateChatUserTyping, UpdateChannelUserTyping]
TypeChatPhoto = Union[ChatPhoto, ChatPhotoEmpty, Photo, PhotoEmpty]
MediaHandler = Callable[["au.AbstractUser", IntentAPI, Message, RelatesTo], Awaitable[EventID]]

REACTION_POLL_MIN_INTERVAL = 20


class BridgingError(Exception):
    pass


class IgnoredMessageError(Exception):
    pass


class Portal(DBPortal, BasePortal):
    bot: "Bot"
    config: Config
    matrix: m.MatrixHandler
    disappearing_msg_class = DisappearingMessage

    # Instance cache
    by_mxid: dict[RoomID, Portal] = {}
    by_tgid: dict[tuple[TelegramID, TelegramID], Portal] = {}

    # Config cache
    filter_mode: str
    filter_list: list[int]

    max_initial_member_sync: int
    sync_channel_members: bool
    sync_matrix_state: bool
    public_portals: bool
    private_chat_portal_meta: bool

    alias_template: SimpleTemplate[str]
    hs_domain: str

    # Instance variables
    deleted: bool

    backfill_lock: SimpleLock
    backfill_method_lock: asyncio.Lock
    backfill_leave: set[IntentAPI] | None

    alias: RoomAlias | None

    dedup: putil.PortalDedup
    send_lock: putil.PortalSendLock
    reaction_lock: putil.PortalReactionLock
    _pin_lock: asyncio.Lock

    _main_intent: IntentAPI | None
    _room_create_lock: asyncio.Lock

    _sponsored_msg: SponsoredMessage | None
    _sponsored_entity: User | Channel | None
    _sponsored_msg_ts: float
    _sponsored_msg_lock: asyncio.Lock
    _sponsored_evt_id: EventID | None
    _sponsored_seen: dict[UserID, bool]
    _new_messages_after_sponsored: bool

    _prev_reaction_poll: dict[UserID, float]

    _msg_conv: putil.TelegramMessageConverter

    def __init__(
        self,
        tgid: TelegramID,
        tg_receiver: TelegramID,
        peer_type: str,
        megagroup: bool = False,
        mxid: RoomID | None = None,
        avatar_url: ContentURI | None = None,
        encrypted: bool = False,
        first_event_id: EventID | None = None,
        next_batch_id: BatchID | None = None,
        base_insertion_id: EventID | None = None,
        sponsored_event_id: EventID | None = None,
        sponsored_event_ts: int | None = None,
        sponsored_msg_random_id: bytes | None = None,
        username: str | None = None,
        title: str | None = None,
        about: str | None = None,
        photo_id: str | None = None,
        name_set: bool = False,
        avatar_set: bool = False,
        local_config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            tgid=tgid,
            tg_receiver=tg_receiver,
            peer_type=peer_type,
            megagroup=megagroup,
            mxid=mxid,
            avatar_url=avatar_url,
            encrypted=encrypted,
            first_event_id=first_event_id,
            next_batch_id=next_batch_id,
            base_insertion_id=base_insertion_id,
            sponsored_event_id=sponsored_event_id,
            sponsored_event_ts=sponsored_event_ts,
            sponsored_msg_random_id=sponsored_msg_random_id,
            username=username,
            title=title,
            about=about,
            photo_id=photo_id,
            name_set=name_set,
            avatar_set=avatar_set,
            local_config=local_config or {},
        )
        BasePortal.__init__(self)
        self.log = self.log.getChild(self.tgid_log if self.tgid else self.mxid)
        self._main_intent = None
        self.deleted = False
        self.backfill_lock = SimpleLock(
            "Waiting for backfilling to finish before handling %s", log=self.log
        )
        self.backfill_method_lock = asyncio.Lock()
        self.backfill_leave = None

        self.dedup = putil.PortalDedup(self)
        self.send_lock = putil.PortalSendLock()
        self.reaction_lock = putil.PortalReactionLock()
        self._pin_lock = asyncio.Lock()
        self._room_create_lock = asyncio.Lock()

        self._sponsored_msg = None
        self._sponsored_msg_ts = 0
        self._sponsored_msg_lock = asyncio.Lock()
        self._sponsored_seen = {}
        self._new_messages_after_sponsored = True
        self._bridging_blocked_at_runtime = False

        self._prev_reaction_poll = defaultdict(lambda: 0.0)

        self._msg_conv = putil.TelegramMessageConverter(self)

    # region Properties

    @property
    def tgid_full(self) -> tuple[TelegramID, TelegramID]:
        return self.tgid, self.tg_receiver

    @property
    def tgid_log(self) -> str:
        if self.tgid == self.tg_receiver:
            return str(self.tgid)
        return f"{self.tg_receiver}<->{self.tgid}"

    @property
    def name(self) -> str:
        return self.title

    @property
    def alias(self) -> RoomAlias | None:
        if not self.username:
            return None
        return RoomAlias(f"#{self.alias_localpart}:{self.hs_domain}")

    @property
    def alias_localpart(self) -> str | None:
        if not self.username:
            return None
        return self.alias_template.format(self.username)

    @property
    def peer(self) -> TypePeer | TypeInputPeer:
        if self.peer_type == "user":
            return PeerUser(user_id=self.tgid)
        elif self.peer_type == "chat":
            return PeerChat(chat_id=self.tgid)
        elif self.peer_type == "channel":
            return PeerChannel(channel_id=self.tgid)

    @property
    def is_direct(self) -> bool:
        return self.peer_type == "user"

    @property
    def has_bot(self) -> bool:
        return bool(self.bot) and (
            self.bot.is_in_chat(self.tgid)
            or (self.peer_type == "user" and self.tg_receiver == self.bot.tgid)
        )

    @property
    def main_intent(self) -> IntentAPI:
        if self._main_intent is None:
            raise RuntimeError("Portal must be postinit()ed before main_intent can be used")
        return self._main_intent

    @property
    def allow_bridging(self) -> bool:
        if self._bridging_blocked_at_runtime:
            return False
        elif self.peer_type == "user":
            return True
        elif self.filter_mode == "whitelist":
            return self.tgid in self.filter_list
        elif self.filter_mode == "blacklist":
            return self.tgid not in self.filter_list
        return True

    @classmethod
    def init_cls(cls, bridge: "TelegramBridge") -> None:
        BasePortal.bridge = bridge
        cls.az = bridge.az
        cls.config = bridge.config
        cls.loop = bridge.loop
        cls.matrix = bridge.matrix
        cls.bot = bridge.bot

        cls.max_initial_member_sync = cls.config["bridge.max_initial_member_sync"]
        cls.sync_channel_members = cls.config["bridge.sync_channel_members"]
        cls.sync_matrix_state = cls.config["bridge.sync_matrix_state"]
        cls.public_portals = cls.config["bridge.public_portals"]
        cls.private_chat_portal_meta = cls.config["bridge.private_chat_portal_meta"]
        cls.filter_mode = cls.config["bridge.filter.mode"]
        cls.filter_list = cls.config["bridge.filter.list"]
        cls.hs_domain = cls.config["homeserver.domain"]
        cls.alias_template = SimpleTemplate(
            cls.config["bridge.alias_template"],
            "groupname",
            prefix="#",
            suffix=f":{cls.hs_domain}",
        )
        NotificationDisabler.puppet_cls = p.Puppet
        NotificationDisabler.config_enabled = cls.config["bridge.backfill.disable_notifications"]

    # endregion
    # region Matrix -> Telegram metadata

    async def save(self) -> None:
        if self.deleted:
            await super().insert()
            await self.postinit()
            self.deleted = False
        else:
            await super().save()

    async def get_telegram_users_in_matrix_room(
        self, source: u.User, pre_create: bool = False
    ) -> tuple[list[InputUser], list[UserID]]:
        user_tgids = {}
        intent = self.az.intent if pre_create else self.main_intent
        user_mxids = await intent.get_room_members(self.mxid, (Membership.JOIN, Membership.INVITE))
        for mxid in user_mxids:
            if mxid == self.az.bot_mxid:
                continue
            mx_user = await u.User.get_by_mxid(mxid, create=False)
            if mx_user and mx_user.tgid:
                user_tgids[mx_user.tgid] = mxid
            puppet_id = p.Puppet.get_id_from_mxid(mxid)
            if puppet_id:
                user_tgids[puppet_id] = mxid
        input_users = []
        errors = []
        for tgid, mxid in user_tgids.items():
            try:
                input_users.append(await source.client.get_input_entity(tgid))
            except ValueError as e:
                source.log.debug(
                    f"Failed to find the input entity for {tgid} ({mxid}) for "
                    f"creating a group: {e}"
                )
                errors.append(mxid)
        return input_users, errors

    async def upgrade_telegram_chat(self, source: u.User) -> None:
        if self.peer_type != "chat":
            raise ValueError("Only normal group chats are upgradable to supergroups.")

        response = await source.client(MigrateChatRequest(chat_id=self.tgid))
        entity = None
        for chat in response.chats:
            if isinstance(chat, Channel):
                entity = chat
                break
        if not entity:
            raise ValueError("Upgrade may have failed: output channel not found.")
        await self._migrate_and_save_telegram(TelegramID(entity.id))
        await self.update_info(source, entity)

    async def _migrate_and_save_telegram(self, new_id: TelegramID) -> None:
        async with self._async_get_locks[(new_id,)]:
            await self._migrate_and_save_telegram_locked(new_id)

    async def _migrate_and_save_telegram_locked(self, new_id: TelegramID) -> None:
        self.log.info(f"Starting migration to {new_id}")
        try:
            del self.by_tgid[self.tgid_full]
        except KeyError:
            pass
        try:
            existing = self.by_tgid[(new_id, new_id)]
        except KeyError:
            existing = None
        self.by_tgid[(new_id, new_id)] = self
        if existing:
            if existing.mxid:
                self.log.warning(f"Deleting existing portal room {existing.mxid} for {new_id}")
                await existing.cleanup_and_delete()
            else:
                self.log.debug(f"Deleting old database entry for {new_id}")
                await existing.delete()
        old_id = self.tgid
        await self.update_id(new_id, "channel")
        self.log = self.__class__.log.getChild(self.tgid_log)
        self.log.info(f"Telegram chat upgraded from {old_id}")

    async def set_telegram_username(self, source: u.User, username: str) -> None:
        if self.peer_type != "channel":
            raise ValueError("Only channels and supergroups have usernames.")
        await source.client(UpdateUsernameRequest(await self.get_input_entity(source), username))
        if await self._update_username(username):
            await self.save()

    async def create_telegram_chat(
        self, source: u.User, invites: list[InputUser], supergroup: bool = False
    ) -> None:
        if not self.mxid:
            raise ValueError("Can't create Telegram chat for portal without Matrix room.")
        elif self.tgid:
            raise ValueError("Can't create Telegram chat for portal with existing Telegram chat.")

        if len(invites) < 2:
            if self.bot is not None:
                info, mxid = await self.bot.get_me()
                raise ValueError(
                    "Not enough Telegram users to create a chat. "
                    "Invite more Telegram ghost users to the room, such as the "
                    f"relaybot ([{info.first_name}](https://matrix.to/#/{mxid}))."
                )
            raise ValueError(
                "Not enough Telegram users to create a chat. "
                "Invite more Telegram ghost users to the room."
            )
        if self.peer_type == "chat":
            response = await source.client(CreateChatRequest(title=self.title, users=invites))
            entity = response.chats[0]
        elif self.peer_type == "channel":
            response = await source.client(
                CreateChannelRequest(
                    title=self.title, about=self.about or "", megagroup=supergroup
                )
            )
            entity = response.chats[0]
            await source.client(
                InviteToChannelRequest(
                    channel=await source.client.get_input_entity(entity), users=invites
                )
            )
        else:
            raise ValueError("Invalid peer type for Telegram chat creation")

        self.tgid = entity.id
        self.tg_receiver = self.tgid
        await self.postinit()
        await self.insert()
        await self.update_info(source, entity)
        self.log = self.__class__.log.getChild(self.tgid_log)

        if self.bot and self.bot.tgid in invites:
            await self.bot.add_chat(self.tgid, self.peer_type)

        levels = await self.main_intent.get_power_levels(self.mxid)
        if levels.get_user_level(self.main_intent.mxid) == 100:
            levels = putil.get_base_power_levels(self, levels, entity)
            await self.main_intent.set_power_levels(self.mxid, levels)
        await self.handle_matrix_power_levels(source, levels.users, {}, None)
        await self.update_bridge_info()
        await self.main_intent.send_notice(self.mxid, f"Telegram chat created. ID: {self.tgid}")

    async def handle_matrix_invite(
        self, invited_by: u.User, puppet: p.Puppet | au.AbstractUser
    ) -> None:
        if isinstance(puppet, p.Puppet) and puppet.is_channel:
            raise ValueError("Can't invite channels to chats")
        try:
            if self.peer_type == "chat":
                await invited_by.client(
                    AddChatUserRequest(chat_id=self.tgid, user_id=puppet.tgid, fwd_limit=0)
                )
            elif self.peer_type == "channel":
                await invited_by.client(
                    InviteToChannelRequest(channel=self.peer, users=[puppet.tgid])
                )
            # We don't care if there are invites for private chat portals with the relaybot.
            elif not self.bot or self.tg_receiver != self.bot.tgid:
                raise RejectMatrixInvite("You can't invite additional users to private chats.")
        except RPCError as e:
            raise RejectMatrixInvite(e.message) from e

    # endregion
    # region Telegram -> Matrix metadata

    def _get_invite_content(self, double_puppet: p.Puppet | None) -> dict[str, Any]:
        invite_content = {}
        if double_puppet:
            invite_content["fi.mau.will_auto_accept"] = True
        if self.is_direct:
            invite_content["is_direct"] = True
        return invite_content

    async def invite_to_matrix(self, users: InviteList) -> None:
        if isinstance(users, list):
            for user in users:
                await self.invite_to_matrix(user)
        else:
            puppet = await p.Puppet.get_by_custom_mxid(users)
            await self.main_intent.invite_user(
                self.mxid, users, check_cache=True, extra_content=self._get_invite_content(puppet)
            )
            if puppet:
                try:
                    await puppet.intent.ensure_joined(self.mxid)
                except Exception:
                    self.log.exception("Failed to ensure %s is joined to portal", users)

    async def update_matrix_room(
        self,
        user: au.AbstractUser,
        entity: TypeChat | User,
        puppet: p.Puppet = None,
        levels: PowerLevelStateEventContent = None,
        users: list[User] = None,
    ) -> None:
        try:
            await self._update_matrix_room(user, entity, puppet, levels, users)
        except Exception:
            self.log.exception("Fatal error updating Matrix room")

    async def _update_matrix_room(
        self,
        user: au.AbstractUser,
        entity: TypeChat | User,
        puppet: p.Puppet = None,
        levels: PowerLevelStateEventContent = None,
        users: list[User] = None,
    ) -> None:
        if not self.is_direct:
            await self.update_info(user, entity)
            if not users:
                users = await self._get_users(user, entity)
            await self._sync_telegram_users(user, users)
            await self.update_power_levels(users, levels)
        else:
            if not puppet:
                puppet = await self.get_dm_puppet()
            await puppet.update_info(user, entity)
            await puppet.intent_for(self).join_room(self.mxid)
            await self.update_info_from_puppet(puppet, user, entity.photo)

            puppet = await p.Puppet.get_by_custom_mxid(user.mxid)
            if puppet:
                try:
                    did_join = await puppet.intent.ensure_joined(self.mxid)
                    if isinstance(user, u.User) and did_join and self.peer_type == "user":
                        await user.update_direct_chats({self.main_intent.mxid: [self.mxid]})
                except Exception:
                    self.log.exception("Failed to ensure %s is joined to portal", user.mxid)

        if self.sync_matrix_state:
            await self.main_intent.get_joined_members(self.mxid)

    async def update_info_from_puppet(
        self,
        puppet: p.Puppet | None = None,
        source: au.AbstractUser | None = None,
        photo: UserProfilePhoto | None = None,
    ) -> None:
        if not self.encrypted and not self.private_chat_portal_meta:
            return
        if puppet is None:
            puppet = await self.get_dm_puppet()
        # The bridge bot needs to join for e2ee, but that messes up the default name
        # generation. If/when canonical DMs happen, this might not be necessary anymore.
        changed = await self._update_avatar_from_puppet(puppet, source, photo)
        changed = await self._update_title(puppet.displayname) or changed
        if changed:
            await self.save()
            await self.update_bridge_info()

    async def create_matrix_room(
        self,
        user: au.AbstractUser,
        entity: TypeChat | User = None,
        invites: InviteList = None,
        update_if_exists: bool = True,
    ) -> RoomID | None:
        if self.mxid:
            if update_if_exists:
                if not entity:
                    try:
                        entity = await self.get_entity(user)
                    except Exception:
                        self.log.exception(f"Failed to get entity through {user.tgid} for update")
                        return self.mxid
                update = self.update_matrix_room(user, entity)
                asyncio.create_task(update)
                await self.invite_to_matrix(invites or [])
            return self.mxid
        async with self._room_create_lock:
            try:
                return await self._create_matrix_room(user, entity, invites)
            except Exception:
                self.log.exception("Fatal error creating Matrix room")

    @property
    def bridge_info_state_key(self) -> str:
        return f"net.maunium.telegram://telegram/{self.tgid}"

    @property
    def bridge_info(self) -> dict[str, Any]:
        info = {
            "bridgebot": self.az.bot_mxid,
            "creator": self.main_intent.mxid,
            "protocol": {
                "id": "telegram",
                "displayname": "Telegram",
                "avatar_url": self.config["appservice.bot_avatar"],
                "external_url": "https://telegram.org",
            },
            "channel": {
                "id": str(self.tgid),
                "displayname": self.title,
                "avatar_url": self.avatar_url,
            },
        }
        if self.username:
            info["channel"]["external_url"] = f"https://t.me/{self.username}"
        elif self.peer_type == "user":
            # TODO this doesn't feel very reliable
            puppet = p.Puppet.by_tgid.get(self.tgid, None)
            if puppet and puppet.username:
                info["channel"]["external_url"] = f"https://t.me/{puppet.username}"
        return info

    async def update_bridge_info(self) -> None:
        if not self.mxid:
            self.log.debug("Not updating bridge info: no Matrix room created")
            return
        try:
            self.log.debug("Updating bridge info...")
            await self.main_intent.send_state_event(
                self.mxid, StateBridge, self.bridge_info, self.bridge_info_state_key
            )
            # TODO remove this once https://github.com/matrix-org/matrix-doc/pull/2346 is in spec
            await self.main_intent.send_state_event(
                self.mxid, StateHalfShotBridge, self.bridge_info, self.bridge_info_state_key
            )
        except Exception:
            self.log.warning("Failed to update bridge info", exc_info=True)

    async def _create_matrix_room(
        self, user: au.AbstractUser, entity: TypeChat | User, invites: InviteList
    ) -> RoomID | None:
        if self.mxid:
            return self.mxid
        elif not self.allow_bridging:
            return None

        invites = invites or []

        if not entity:
            entity = await self.get_entity(user)
            self.log.trace("Fetched data: %s", entity)

        participants_count = 2
        if isinstance(entity, Chat):
            participants_count = entity.participants_count
        elif isinstance(entity, Channel) and not entity.broadcast:
            participants_count = entity.participants_count
        if participants_count is None and self.config["bridge.max_member_count"] > 0:
            self.log.warning(f"Participant count not found in entity, fetching manually")
            participants_count = (await user.client.get_participants(entity, limit=0)).total
        if participants_count and 0 < self.config["bridge.max_member_count"] < participants_count:
            self.log.warning(f"Not bridging chat, too many participants (%d)", participants_count)
            self._bridging_blocked_at_runtime = True
            return None

        self.log.debug("Creating room")

        try:
            self.title = entity.title
        except AttributeError:
            self.title = None

        if self.is_direct and self.tgid == user.tgid:
            self.title = "Telegram Saved Messages"
            self.about = "Your Telegram cloud storage chat"

        puppet = await self.get_dm_puppet()
        if puppet:
            await puppet.update_info(user, entity)
        self._main_intent = puppet.intent_for(self) if self.is_direct else self.az.intent

        if self.peer_type == "channel":
            self.megagroup = entity.megagroup

        preset = RoomCreatePreset.PRIVATE
        if self.peer_type == "channel" and entity.username:
            if self.public_portals:
                preset = RoomCreatePreset.PUBLIC
            self.username = entity.username
            alias = self.alias_localpart
        else:
            # TODO invite link alias?
            alias = None

        if alias:
            # TODO? properly handle existing room aliases
            await self.main_intent.remove_room_alias(alias)

        power_levels = putil.get_base_power_levels(self, entity=entity)
        users = None
        if not self.is_direct:
            users = await self._get_users(user, entity)
            if self.has_bot:
                extra_invites = self.config["bridge.relaybot.group_chat_invite"]
                invites += extra_invites
                for invite in extra_invites:
                    power_levels.users.setdefault(invite, 100)
            await putil.participants_to_power_levels(self, users, power_levels)
        elif self.bot and self.tg_receiver == self.bot.tgid:
            invites = self.config["bridge.relaybot.private_chat.invite"]
            for invite in invites:
                power_levels.users.setdefault(invite, 100)
            self.title = puppet.displayname

        initial_state = [
            {
                "type": EventType.ROOM_POWER_LEVELS.serialize(),
                "content": power_levels.serialize(),
            },
            {
                "type": str(StateBridge),
                "state_key": self.bridge_info_state_key,
                "content": self.bridge_info,
            },
            # TODO remove this once https://github.com/matrix-org/matrix-doc/pull/2346 is in spec
            {
                "type": str(StateHalfShotBridge),
                "state_key": self.bridge_info_state_key,
                "content": self.bridge_info,
            },
        ]
        create_invites = []
        if self.config["bridge.encryption.default"] and self.matrix.e2ee:
            self.encrypted = True
            initial_state.append(
                {
                    "type": str(EventType.ROOM_ENCRYPTION),
                    "content": self.get_encryption_state_event_json(),
                }
            )
            if self.is_direct:
                create_invites.append(self.az.bot_mxid)
        if self.is_direct and (self.encrypted or self.private_chat_portal_meta):
            self.title = puppet.displayname
            self.avatar_url = puppet.avatar_url
            self.photo_id = puppet.photo_id
        creation_content = {}
        if not self.config["bridge.federate_rooms"]:
            creation_content["m.federate"] = False
        if self.avatar_url:
            initial_state.append(
                {
                    "type": str(EventType.ROOM_AVATAR),
                    "content": {"url": self.avatar_url},
                }
            )

        with self.backfill_lock:
            room_id = await self.main_intent.create_room(
                alias_localpart=alias,
                preset=preset,
                is_direct=self.is_direct,
                invitees=create_invites,
                name=self.title,
                topic=self.about,
                initial_state=initial_state,
                creation_content=creation_content,
            )
            if not room_id:
                raise Exception(f"Failed to create room")
            self.name_set = bool(self.title)
            self.avatar_set = bool(self.avatar_url)

            if self.encrypted and self.matrix.e2ee and self.is_direct:
                try:
                    await self.az.intent.ensure_joined(room_id)
                except Exception:
                    self.log.warning(f"Failed to add bridge bot to new private chat {room_id}")

            self.mxid = room_id
            self.by_mxid[self.mxid] = self
            self.first_event_id = await self.main_intent.send_message_event(
                self.mxid, DummyPortalCreated, {}
            )
            await self.save()
            self.log.debug(f"Matrix room created: {self.mxid}")
            await self.az.state_store.set_power_levels(self.mxid, power_levels)
            await user.register_portal(self)

            await self.invite_to_matrix(invites)

            update_room = asyncio.create_task(
                self.update_matrix_room(user, entity, puppet, levels=power_levels, users=users)
            )

            if self.config["bridge.backfill.initial_limit"] > 0:
                self.log.debug(
                    "Initial backfill is enabled. Waiting for room members to sync "
                    "and then starting backfill"
                )
                await update_room

                try:
                    if isinstance(user, u.User):
                        await self.backfill(user, is_initial=True)
                except Exception:
                    self.log.exception("Failed to backfill new portal")

        return self.mxid

    async def _get_users(
        self,
        user: au.AbstractUser,
        entity: TypeInputPeer | InputUser | TypeChat | TypeUser | InputChannel,
    ) -> list[TypeUser]:
        if self.peer_type == "channel" and not self.megagroup and not self.sync_channel_members:
            return []
        limit = self.max_initial_member_sync
        if limit == 0:
            return []
        return await putil.get_users(user.client, self.tgid, entity, limit, self.peer_type)

    async def update_power_levels(
        self,
        users: list[TypeUser | TypeChatParticipant | TypeChannelParticipant],
        levels: PowerLevelStateEventContent = None,
    ) -> None:
        if not levels:
            levels = await self.main_intent.get_power_levels(self.mxid)
        if await putil.participants_to_power_levels(self, users, levels):
            await self.main_intent.set_power_levels(self.mxid, levels)

    async def _add_bot_chat(self, bot: User) -> None:
        if self.bot and bot.id == self.bot.tgid:
            await self.bot.add_chat(self.tgid, self.peer_type)
            return

        user = await u.User.get_by_tgid(TelegramID(bot.id))
        if user and user.is_bot:
            await user.register_portal(self)

    async def _sync_telegram_users(self, source: au.AbstractUser, users: list[User]) -> None:
        allowed_tgids = set()
        skip_deleted = self.config["bridge.skip_deleted_members"]
        for entity in users:
            puppet = await p.Puppet.get_by_tgid(TelegramID(entity.id))
            if entity.bot:
                await self._add_bot_chat(entity)
            allowed_tgids.add(entity.id)

            await puppet.update_info(source, entity)
            if skip_deleted and entity.deleted:
                continue

            await puppet.intent_for(self).ensure_joined(self.mxid)

            user = await u.User.get_by_tgid(TelegramID(entity.id))
            if user:
                await self.invite_to_matrix(user.mxid)

        # We can't trust the member list if any of the following cases is true:
        #  * There are close to 10 000 users, because Telegram might not be sending all members.
        #  * The member sync count is limited, because then we might ignore some members.
        #  * It's a channel, because non-admins don't have access to the member list.
        trust_member_list = (
            len(allowed_tgids) < 9900
            if self.max_initial_member_sync < 0
            else len(allowed_tgids) < self.max_initial_member_sync - 10
        ) and (self.megagroup or self.peer_type != "channel")
        if not trust_member_list:
            return

        for user_mxid in await self.main_intent.get_room_members(self.mxid):
            if user_mxid == self.az.bot_mxid:
                continue

            puppet = await p.Puppet.get_by_mxid(user_mxid)
            if puppet:
                # TODO figure out when/how to clean up channels from the member list
                if puppet.id in allowed_tgids or puppet.is_channel:
                    continue
                if self.bot and puppet.id == self.bot.tgid:
                    await self.bot.remove_chat(self.tgid)
                try:
                    await self.main_intent.kick_user(
                        self.mxid, user_mxid, "User had left this Telegram chat."
                    )
                except MForbidden:
                    pass
                continue

            mx_user = await u.User.get_by_mxid(user_mxid, create=False)
            if mx_user:
                if mx_user.tgid in allowed_tgids:
                    continue
                if mx_user.is_bot:
                    await mx_user.unregister_portal(*self.tgid_full)
                if not self.has_bot:
                    try:
                        await self.main_intent.kick_user(
                            self.mxid, mx_user.mxid, "You had left this Telegram chat."
                        )
                    except MForbidden:
                        pass

    async def _add_telegram_user(
        self, user_id: TelegramID, source: au.AbstractUser | None = None
    ) -> None:
        puppet = await p.Puppet.get_by_tgid(user_id)
        if source:
            try:
                entity: User = await source.client.get_entity(PeerUser(user_id))
            except ValueError:
                self.log.warning(
                    f"Couldn't get info of {user_id} through {source.tgid} to add them to the room"
                )
                return
            await puppet.update_info(source, entity)
            await puppet.intent_for(self).ensure_joined(self.mxid)

        user = await u.User.get_by_tgid(user_id)
        if user:
            await user.register_portal(self)
            await self.invite_to_matrix(user.mxid)

    async def delete_telegram_user(self, user_id: TelegramID, sender: p.Puppet | None) -> None:
        puppet = await p.Puppet.get_by_tgid(user_id)
        if sender is None:
            sender = puppet
        user = await u.User.get_by_tgid(user_id)
        kick_message = (
            f"Kicked by {sender.displayname}"
            if sender and sender.tgid != puppet.tgid
            else "Left Telegram chat"
        )
        puppet_extra_content = None
        if sender.is_real_user:
            puppet_extra_content = {DOUBLE_PUPPET_SOURCE_KEY: self.bridge.name}
        if sender.tgid != puppet.tgid:
            try:
                await sender.intent_for(self).kick_user(
                    self.mxid, puppet.mxid, extra_content=puppet_extra_content
                )
            except MForbidden:
                try:
                    await self.main_intent.kick_user(self.mxid, puppet.mxid, kick_message)
                except MForbidden as e:
                    self.log.warning(f"Failed to kick {puppet.mxid}: {e}")
        elif await self.az.state_store.is_joined(self.mxid, puppet.intent_for(self).mxid):
            await puppet.intent_for(self).leave_room(self.mxid, extra_content=puppet_extra_content)
        if user:
            await user.unregister_portal(*self.tgid_full)
            if sender.tgid != puppet.tgid:
                try:
                    await sender.intent_for(self).kick_user(
                        self.mxid, user.mxid, extra_content=puppet_extra_content
                    )
                    return
                except MForbidden:
                    pass
            try:
                await self.main_intent.kick_user(self.mxid, user.mxid, kick_message)
            except MForbidden as e:
                self.log.warning(f"Failed to kick {user.mxid}: {e}")

    async def update_info(self, user: au.AbstractUser, entity: TypeChat = None) -> None:
        if self.peer_type == "user":
            self.log.warning("Called update_info() for direct chat portal")
            return

        changed = False
        self.log.debug("Updating info")
        try:
            if not entity:
                entity = await self.get_entity(user)
                self.log.trace("Fetched data: %s", entity)

            if self.peer_type == "channel":
                changed = self.megagroup != entity.megagroup or changed
                self.megagroup = entity.megagroup
                changed = await self._update_username(entity.username) or changed

            if hasattr(entity, "about"):
                changed = self._update_about(entity.about) or changed

            changed = await self._update_title(entity.title) or changed

            if isinstance(entity.photo, ChatPhoto):
                changed = await self._update_avatar(user, entity.photo) or changed
        except Exception:
            self.log.exception(f"Failed to update info from source {user.tgid}")

        if changed:
            await self.save()
            await self.update_bridge_info()

    async def _update_username(self, username: str, save: bool = False) -> bool:
        if self.username == username:
            return False

        if self.username:
            await self.main_intent.remove_room_alias(self.alias_localpart)
        self.username = username or None
        if self.username:
            await self.main_intent.add_room_alias(self.mxid, self.alias_localpart, override=True)
            if self.public_portals:
                await self.main_intent.set_join_rule(self.mxid, JoinRule.PUBLIC)
        else:
            await self.main_intent.set_join_rule(self.mxid, JoinRule.INVITE)

        if save:
            await self.save()
        return True

    async def _try_set_state(
        self, sender: p.Puppet | None, evt_type: EventType, content: StateEventContent
    ) -> None:
        if sender:
            try:
                intent = sender.intent_for(self)
                if sender.is_real_user:
                    content[DOUBLE_PUPPET_SOURCE_KEY] = self.bridge.name
                await intent.send_state_event(self.mxid, evt_type, content)
            except MForbidden:
                await self.main_intent.send_state_event(self.mxid, evt_type, content)
        else:
            await self.main_intent.send_state_event(self.mxid, evt_type, content)

    async def _update_about(
        self, about: str, sender: p.Puppet | None = None, save: bool = False
    ) -> bool:
        if self.about == about:
            return False

        self.about = about
        if self.mxid:
            await self._try_set_state(
                sender, EventType.ROOM_TOPIC, RoomTopicStateEventContent(topic=self.about)
            )
        if save:
            await self.save()
        return True

    async def _update_title(
        self, title: str, sender: p.Puppet | None = None, save: bool = False
    ) -> bool:
        if self.title == title and self.name_set:
            return False

        self.title = title
        if self.mxid:
            try:
                await self._try_set_state(
                    sender, EventType.ROOM_NAME, RoomNameStateEventContent(name=self.title)
                )
                self.name_set = True
            except Exception as e:
                self.log.warning(f"Failed to set room name: {e}")
                self.name_set = False
        if save:
            await self.save()
        return True

    async def _update_avatar_from_puppet(
        self, puppet: p.Puppet, user: au.AbstractUser | None, photo: UserProfilePhoto | None
    ) -> bool:
        if self.photo_id == puppet.photo_id and self.avatar_set:
            return False
        if puppet.avatar_url:
            self.photo_id = puppet.photo_id
            self.avatar_url = puppet.avatar_url
            if self.mxid:
                try:
                    await self._try_set_state(
                        None,
                        EventType.ROOM_AVATAR,
                        RoomAvatarStateEventContent(url=self.avatar_url),
                    )
                    self.avatar_set = True
                except Exception as e:
                    self.log.warning(f"Failed to set room avatar: {e}")
                    self.avatar_set = False
            return True
        elif photo is not None and user is not None:
            return await self._update_avatar(user, photo=photo)
        else:
            return False

    async def _update_avatar(
        self,
        user: au.AbstractUser,
        photo: TypeChatPhoto | TypeUserProfilePhoto,
        sender: p.Puppet | None = None,
        save: bool = False,
    ) -> bool:
        if isinstance(photo, (ChatPhoto, UserProfilePhoto)):
            loc = InputPeerPhotoFileLocation(
                peer=await self.get_input_entity(user), photo_id=photo.photo_id, big=True
            )
            photo_id = str(photo.photo_id)
        elif isinstance(photo, Photo):
            loc, _ = self._msg_conv.get_largest_photo_size(photo)
            photo_id = str(loc.id)
        elif isinstance(photo, (UserProfilePhotoEmpty, ChatPhotoEmpty, PhotoEmpty, type(None))):
            photo_id = ""
            loc = None
        else:
            raise ValueError(f"Unknown photo type {type(photo)}")
        if (
            self.peer_type == "user"
            and not photo_id
            and not self.config["bridge.allow_avatar_remove"]
        ):
            return False
        if self.photo_id != photo_id or not self.avatar_set:
            if not photo_id:
                self.photo_id = ""
                self.avatar_url = None
            elif self.photo_id != photo_id or not self.avatar_url:
                file = await util.transfer_file_to_matrix(
                    user.client,
                    self.main_intent,
                    loc,
                    async_upload=self.config["homeserver.async_media"],
                )
                if not file:
                    return False
                self.photo_id = photo_id
                self.avatar_url = file.mxc
            if self.mxid:
                try:
                    await self._try_set_state(
                        sender,
                        EventType.ROOM_AVATAR,
                        RoomAvatarStateEventContent(url=self.avatar_url),
                    )
                    self.avatar_set = True
                except Exception as e:
                    self.log.warning(f"Failed to set room avatar: {e}")
                    self.avatar_set = False
            if save:
                await self.save()
            return True
        return False

    # endregion
    # region Matrix -> Telegram bridging

    async def _send_delivery_receipt(
        self, event_id: EventID, room_id: RoomID | None = None
    ) -> None:
        if event_id and self.config["bridge.delivery_receipts"]:
            try:
                await self.az.intent.mark_read(room_id or self.mxid, event_id)
            except Exception:
                self.log.exception("Failed to send delivery receipt for %s", event_id)

    async def _get_state_change_message(
        self, event: str, user: u.User, **kwargs: Any
    ) -> str | None:
        tpl = self.get_config(f"state_event_formats.{event}")
        if len(tpl) == 0:
            # Empty format means they don't want the message
            return None
        displayname = await self.get_displayname(user)

        tpl_args = {
            "mxid": user.mxid,
            "username": user.mxid_localpart,
            "displayname": escape_html(displayname),
            "distinguisher": self._get_distinguisher(user.mxid),
            **kwargs,
        }
        return Template(tpl).safe_substitute(tpl_args)

    async def _send_state_change_message(
        self, event: str, user: u.User, event_id: EventID, **kwargs: Any
    ) -> None:
        if not self.has_bot:
            return
        elif (
            self.peer_type == "user"
            and not self.config["bridge.relaybot.private_chat.state_changes"]
        ):
            return
        async with self.send_lock(self.bot.tgid):
            message = await self._get_state_change_message(event, user, **kwargs)
            if not message:
                return
            message, entities = await formatter.matrix_to_telegram(self.bot.client, html=message)
            response = await self.bot.client.send_message(
                self.peer, message, formatting_entities=entities
            )
            space = self.tgid if self.peer_type == "channel" else self.bot.tgid
            self.dedup.check(response, (event_id, space))

    async def name_change_matrix(
        self, user: u.User, displayname: str, prev_displayname: str, event_id: EventID
    ) -> None:
        await self._send_state_change_message(
            "name_change",
            user,
            event_id,
            displayname=displayname,
            prev_displayname=prev_displayname,
        )

    async def get_displayname(self, user: u.User) -> str:
        return await self.main_intent.get_room_displayname(self.mxid, user.mxid) or user.mxid

    def set_typing(
        self, user: u.User, typing: bool = True, action: type = SendMessageTypingAction
    ) -> Awaitable[bool]:
        return user.client(
            SetTypingRequest(self.peer, action() if typing else SendMessageCancelAction())
        )

    async def _get_sponsored_message(
        self, user: u.User
    ) -> tuple[SponsoredMessage | None, Channel | User | None]:
        if user.is_bot:
            return None, None
        elif self._sponsored_msg_ts + 5 * 60 > time.monotonic():
            return self._sponsored_msg, self._sponsored_entity

        self.log.trace(f"Fetching a new sponsored message through {user.mxid}")
        self._sponsored_msg, t_id, self._sponsored_entity = await putil.get_sponsored_message(
            user, await self.get_input_entity(user)
        )
        self._sponsored_msg_ts = time.monotonic()
        if self._sponsored_msg is not None and self._sponsored_entity is None:
            self.log.warning(f"GetSponsoredMessages didn't return entity for {t_id}")
        return self._sponsored_msg, self._sponsored_entity

    async def _send_sponsored_msg(self, user: u.User) -> None:
        msg, entity = await self._get_sponsored_message(user)
        if msg is None:
            self.log.trace("Didn't get a sponsored message")
            return
        if self.sponsored_event_id is not None:
            self.log.debug(
                f"Redacting old sponsored {self.sponsored_event_id}"
                " in preparation for sending new one"
            )
            await self.main_intent.redact(self.mxid, self.sponsored_event_id)
        content = await putil.make_sponsored_message_content(user, msg, entity)
        self.log.trace("Sending sponsored message")
        self.sponsored_event_id = await self._send_message(self.main_intent, content)
        self.sponsored_event_ts = int(time.time())
        self.sponsored_msg_random_id = msg.random_id
        self._new_messages_after_sponsored = False
        self._sponsored_seen = {}
        await self.save()
        self.log.debug(
            f"Sent sponsored message {base64.b64encode(self.sponsored_msg_random_id)} "
            f"to Matrix {self.sponsored_event_id} / {self.sponsored_event_ts}"
        )

    @property
    def _sponsored_is_expired(self) -> bool:
        return (
            self.sponsored_event_id is None
            or self.sponsored_event_ts + 24 * 60 * 60 < int(time.time())
        ) and self._new_messages_after_sponsored

    async def _try_handle_read_for_sponsored_msg(
        self, user: u.User, event_id: EventID, timestamp: int
    ) -> None:
        try:
            await self._handle_read_for_sponsored_msg(user, event_id, timestamp)
        except Exception:
            self.log.warning(
                "Error handling read receipt for sponsored message processing", exc_info=True
            )

    async def _handle_read_for_sponsored_msg(
        self, user: u.User, event_id: EventID, timestamp: int
    ) -> None:
        if user.is_bot or not self.username:
            return
        if self._sponsored_is_expired:
            self.log.trace("Sponsored message is expired, sending new one")
            async with self._sponsored_msg_lock:
                if self._sponsored_is_expired:
                    await self._send_sponsored_msg(user)
                    return

        if (
            self.sponsored_event_id == event_id or self.sponsored_event_ts <= timestamp
        ) and not self._sponsored_seen.get(user.mxid, False):
            self._sponsored_seen[user.mxid] = True
            self.log.debug(
                f"Marking sponsored message {self.sponsored_event_id} as seen by {user.mxid}"
            )
            await user.client(
                ViewSponsoredMessageRequest(
                    channel=await self.get_input_entity(user),
                    random_id=self.sponsored_msg_random_id,
                )
            )

    async def mark_read(self, user: u.User, event_id: EventID, timestamp: int) -> None:
        if user.is_bot:
            return
        space = self.tgid if self.peer_type == "channel" else user.tgid
        message = await DBMessage.get_by_mxid(event_id, self.mxid, space)
        if not message:
            message = await DBMessage.find_last(self.mxid, space)
            if not message:
                self.log.debug(
                    f"Dropping Matrix read receipt from {user.mxid}: "
                    f"target message {event_id} not known and last message in chat not found"
                )
                return
            else:
                self.log.debug(
                    f"Matrix read receipt target {event_id} not known, marking "
                    f"messages up to most recent ({message.mxid}/{message.tgid}) "
                    f"as read by {user.mxid}/{user.tgid}"
                )
        else:
            self.log.debug(
                "Handling Matrix read receipt: marking messages up to "
                f"{message.mxid}/{message.tgid} as read by {user.mxid}/{user.tgid}"
            )
        await user.client.send_read_acknowledge(
            self.peer, max_id=message.tgid, clear_mentions=True, clear_reactions=True
        )
        if self.peer_type == "channel":
            if not self.megagroup:
                asyncio.create_task(
                    self._try_handle_read_for_sponsored_msg(user, event_id, timestamp)
                )
            else:
                asyncio.create_task(self._poll_telegram_reactions(user))

    async def _preproc_kick_ban(
        self, user: u.User | p.Puppet, source: u.User
    ) -> au.AbstractUser | None:
        if user.tgid == source.tgid:
            return None
        if self.peer_type == "user" and user.tgid == self.tgid:
            await self.delete()
            return None
        if isinstance(user, u.User) and await user.needs_relaybot(self):
            if not self.bot:
                return None
            # TODO kick message
            return None
        if await source.needs_relaybot(self):
            if not self.has_bot:
                return None
            return self.bot
        return source

    async def kick_matrix(self, user: u.User | p.Puppet, source: u.User) -> None:
        source = await self._preproc_kick_ban(user, source)
        if source is not None:
            await source.client.kick_participant(self.peer, user.peer)

    async def ban_matrix(self, user: u.User | p.Puppet, source: u.User):
        source = await self._preproc_kick_ban(user, source)
        if source is not None:
            await source.client.edit_permissions(self.peer, user.peer, view_messages=False)

    async def leave_matrix(self, user: u.User, event_id: EventID) -> None:
        if await user.needs_relaybot(self):
            await self._send_state_change_message("leave", user, event_id)
            return

        if self.peer_type == "user":
            await self.main_intent.leave_room(self.mxid)
            await self.delete()
            try:
                del self.by_tgid[self.tgid_full]
                del self.by_mxid[self.mxid]
            except KeyError:
                pass
        elif self.config["bridge.bridge_matrix_leave"]:
            await user.client.delete_dialog(self.peer)

    async def join_matrix(self, user: u.User, event_id: EventID) -> None:
        if await user.needs_relaybot(self):
            await self._send_state_change_message("join", user, event_id)
            return

        if self.peer_type == "channel" and not user.is_bot:
            await user.client(JoinChannelRequest(channel=await self.get_input_entity(user)))
        else:
            # We'll just assume the user is already in the chat.
            pass

    @staticmethod
    def hash_user_id(val: UserID) -> int:
        """
        A simple Matrix user ID hashing algorithm that matches what Element does.

        Args:
            val: the Matrix user ID.

        Returns:
            A 32-bit hash of the user ID.
        """
        out = 0
        for char in val:
            out = (out << 5) - out + ord(char)
            # Emulate JS's 32-bit signed bitwise OR `hash |= 0`
            out = (out & 2**31 - 1) - (out & 2**31)
        return abs(out)

    def _get_distinguisher(self, user_id: UserID) -> str:
        ruds = self.get_config("relay_user_distinguishers") or []
        return ruds[self.hash_user_id(user_id) % len(ruds)] if ruds else ""

    async def _apply_msg_format(self, sender: u.User, content: MessageEventContent) -> None:
        if isinstance(content, TextMessageEventContent):
            content.ensure_has_html()
        else:
            content.format = Format.HTML
            content.formatted_body = escape_html(content.body).replace("\n", "<br/>")

        tpl = (
            self.get_config(f"message_formats.[{content.msgtype.value}]")
            or "<b>$sender_displayname</b>: $message"
        )
        displayname = await self.get_displayname(sender)
        tpl_args = dict(
            sender_mxid=sender.mxid,
            sender_username=sender.mxid_localpart,
            sender_displayname=escape_html(displayname),
            message=content.formatted_body,
            body=content.body,
            formatted_body=content.formatted_body,
            distinguisher=self._get_distinguisher(sender.mxid),
        )
        content.formatted_body = Template(tpl).safe_substitute(tpl_args)

    async def _apply_emote_format(self, sender: u.User, content: TextMessageEventContent) -> None:
        content.ensure_has_html()

        tpl = self.get_config("emote_format")
        puppet = await p.Puppet.get_by_tgid(sender.tgid)
        content.formatted_body = Template(tpl).safe_substitute(
            dict(
                sender_mxid=sender.mxid,
                sender_username=sender.mxid_localpart,
                sender_displayname=escape_html(await self.get_displayname(sender)),
                mention=f"<a href='https://matrix.to/#/{puppet.mxid}'>{puppet.displayname}</a>",
                username=sender.tg_username,
                displayname=puppet.displayname,
                body=content.body,
                formatted_body=content.formatted_body,
            )
        )
        content.msgtype = MessageType.TEXT

    async def _pre_process_matrix_message(
        self, sender: u.User, use_relaybot: bool, content: MessageEventContent
    ) -> None:
        if use_relaybot:
            await self._apply_msg_format(sender, content)
        elif content.msgtype == MessageType.EMOTE:
            await self._apply_emote_format(sender, content)

    async def _handle_matrix_text(
        self,
        sender: u.User,
        logged_in: bool,
        event_id: EventID,
        space: TelegramID,
        client: MautrixTelegramClient,
        content: TextMessageEventContent,
        reply_to: TelegramID | None,
    ) -> None:
        message, entities = await formatter.matrix_to_telegram(
            client, text=content.body, html=content.formatted(Format.HTML)
        )
        sender_id = sender.tgid if logged_in else self.bot.tgid
        async with self.send_lock(sender_id):
            lp = self.get_config("telegram_link_preview")
            if content.get_edit():
                orig_msg = await DBMessage.get_by_mxid(content.get_edit(), self.mxid, space)
                if orig_msg:
                    resp = await client.edit_message(
                        self.peer,
                        orig_msg.tgid,
                        message,
                        formatting_entities=entities,
                        link_preview=lp,
                    )
                    await self._mark_matrix_handled(
                        sender=sender,
                        sender_tgid=sender_id,
                        event_type=EventType.ROOM_MESSAGE,
                        event_id=event_id,
                        space=space,
                        edit_index=-1,
                        response=resp,
                        msgtype=content.msgtype,
                    )
                    return
            response = await client.send_message(
                self.peer,
                message,
                reply_to=reply_to,
                formatting_entities=entities,
                link_preview=lp,
            )
            await self._mark_matrix_handled(
                sender=sender,
                sender_tgid=sender_id,
                event_type=EventType.ROOM_MESSAGE,
                event_id=event_id,
                space=space,
                edit_index=0,
                response=response,
                msgtype=content.msgtype,
            )

    async def _handle_matrix_file(
        self,
        sender: u.User,
        logged_in: bool,
        event_id: EventID,
        space: TelegramID,
        client: MautrixTelegramClient,
        content: MediaMessageEventContent,
        reply_to: TelegramID,
        file_name: str,
        caption: TextMessageEventContent = None,
    ) -> None:
        sender_id = sender.tgid if logged_in else self.bot.tgid
        mime = content.info.mimetype
        if isinstance(content.info, (ImageInfo, VideoInfo)):
            w, h = content.info.width, content.info.height
        else:
            w = h = None
        max_image_size = self.config["bridge.image_as_file_size"] * 1000**2
        max_image_pixels = self.config["bridge.image_as_file_pixels"]

        if self.config["bridge.parallel_file_transfer"] and content.url:
            file_handle, file_size = await util.parallel_transfer_to_telegram(
                client, self.main_intent, content.url, sender_id
            )
        else:
            if content.file:
                if not decrypt_attachment:
                    raise BridgingError(
                        f"Can't bridge encrypted media event {event_id}: "
                        "encryption dependencies not installed"
                    )
                file = await self.main_intent.download_media(content.file.url)
                file = decrypt_attachment(
                    file, content.file.key.key, content.file.hashes.get("sha256"), content.file.iv
                )
            else:
                file = await self.main_intent.download_media(content.url)

            if content.msgtype == MessageType.STICKER:
                if mime != "image/gif":
                    mime, file, w, h = util.convert_image(
                        file, source_mime=mime, target_type="webp"
                    )
                else:
                    # Remove sticker description
                    file_name = "sticker.gif"

            file_handle = await client.upload_file(file)
            file_size = len(file)

        file_handle.name = file_name
        force_document = file_size >= max_image_size

        attributes = [DocumentAttributeFilename(file_name=file_name)]
        if content.msgtype == MessageType.VIDEO:
            attributes.append(
                DocumentAttributeVideo(
                    duration=content.info.duration // 1000 if content.info.duration else 0,
                    w=w or 0,
                    h=h or 0,
                )
            )
        elif content.msgtype == MessageType.AUDIO:
            waveform = content.get("org.matrix.msc1767.audio", {}).get("waveform", [])
            if waveform:
                waveform_max = max(waveform)
                waveform = [round(part / max(waveform_max / 32, 1)) for part in waveform]
            attributes.append(
                DocumentAttributeAudio(
                    duration=content.info.duration // 1000 if content.info.duration else 0,
                    voice="org.matrix.msc3245.voice" in content,
                    waveform=encode_waveform(waveform) if waveform else None,
                )
            )
        elif w and h:
            attributes.append(DocumentAttributeImageSize(w, h))
            force_document = force_document or w * h >= max_image_pixels

        if "fi.mau.telegram.force_document" in content:
            force_document = bool(content["fi.mau.telegram.force_document"])

        if (mime == "image/png" or mime == "image/jpeg") and not force_document:
            media = InputMediaUploadedPhoto(file_handle)
        else:
            media = InputMediaUploadedDocument(
                file=file_handle,
                attributes=attributes,
                mime_type=mime or "application/octet-stream",
            )

        capt, entities = (
            await formatter.matrix_to_telegram(
                client, text=caption.body, html=caption.formatted(Format.HTML)
            )
            if caption
            else (None, None)
        )

        async with self.send_lock(sender_id):
            if await self._matrix_document_edit(
                sender, sender_id, client, content, space, capt, entities, media, event_id
            ):
                return
            try:
                try:
                    response = await client.send_media(
                        self.peer, media, reply_to=reply_to, caption=capt, entities=entities
                    )
                except (
                    PhotoInvalidDimensionsError,
                    PhotoSaveFileInvalidError,
                    PhotoExtInvalidError,
                ):
                    media = InputMediaUploadedDocument(
                        file=media.file, mime_type=mime, attributes=attributes
                    )
                    response = await client.send_media(
                        self.peer, media, reply_to=reply_to, caption=capt, entities=entities
                    )
            except Exception:
                raise
            else:
                await self._mark_matrix_handled(
                    sender=sender,
                    sender_tgid=sender_id,
                    event_type=EventType.ROOM_MESSAGE,
                    event_id=event_id,
                    space=space,
                    edit_index=0,
                    response=response,
                    msgtype=content.msgtype,
                )

    async def _matrix_document_edit(
        self,
        sender: u.User,
        sender_tgid: TelegramID,
        client: MautrixTelegramClient,
        content: MessageEventContent,
        space: TelegramID,
        caption: str,
        caption_entities,
        media: Any,
        event_id: EventID,
    ) -> bool:
        if content.get_edit():
            orig_msg = await DBMessage.get_by_mxid(content.get_edit(), self.mxid, space)
            if orig_msg:
                response = await client.edit_message(
                    self.peer,
                    orig_msg.tgid,
                    caption,
                    formatting_entities=caption_entities,
                    file=media,
                )
                await self._mark_matrix_handled(
                    sender=sender,
                    sender_tgid=sender_tgid,
                    event_type=EventType.ROOM_MESSAGE,
                    event_id=event_id,
                    space=space,
                    edit_index=-1,
                    response=response,
                    msgtype=content.msgtype,
                )
                return True
        return False

    async def _handle_matrix_location(
        self,
        sender: u.User,
        logged_in: bool,
        event_id: EventID,
        space: TelegramID,
        client: MautrixTelegramClient,
        content: LocationMessageEventContent,
        reply_to: TelegramID,
    ) -> None:
        sender_id = sender.tgid if logged_in else self.bot.tgid
        try:
            lat, long = content.geo_uri[len("geo:") :].split(";")[0].split(",")
            lat, long = float(lat), float(long)
        except (KeyError, ValueError):
            self.log.exception("Failed to parse location")
            return None
        try:
            caption = content["org.matrix.msc3488.location"]["description"]
            entities = []
        except KeyError:
            caption, entities = await formatter.matrix_to_telegram(client, text=content.body)
        media = MessageMediaGeo(geo=GeoPoint(lat=lat, long=long, access_hash=0))

        async with self.send_lock(sender_id):
            if await self._matrix_document_edit(
                sender, sender_id, client, content, space, caption, entities, media, event_id
            ):
                return
            try:
                response = await client.send_media(
                    self.peer, media, reply_to=reply_to, caption=caption, entities=entities
                )
            except Exception:
                raise
            else:
                await self._mark_matrix_handled(
                    sender=sender,
                    sender_tgid=sender_id,
                    event_type=EventType.ROOM_MESSAGE,
                    event_id=event_id,
                    space=space,
                    edit_index=0,
                    response=response,
                    msgtype=content.msgtype,
                )

    async def _mark_matrix_handled(
        self,
        sender: u.User,
        sender_tgid: TelegramID,
        event_type: EventType,
        event_id: EventID,
        space: TelegramID,
        edit_index: int,
        response: TypeMessage,
        msgtype: MessageType | None = None,
    ) -> None:
        self.log.trace("Handled Matrix message: %s", response)
        event_hash, _ = self.dedup.check(response, (event_id, space), force_hash=edit_index != 0)
        if edit_index < 0:
            prev_edit = await DBMessage.get_one_by_tgid(TelegramID(response.id), space, -1)
            edit_index = prev_edit.edit_index + 1
        await DBMessage(
            tgid=TelegramID(response.id),
            tg_space=space,
            mx_room=self.mxid,
            mxid=event_id,
            edit_index=edit_index,
            content_hash=event_hash,
            sender_mxid=sender.mxid,
            sender=sender_tgid,
        ).insert()
        sender.send_remote_checkpoint(
            MessageSendCheckpointStatus.SUCCESS,
            event_id,
            self.mxid,
            event_type,
            message_type=msgtype,
        )
        await self._send_delivery_receipt(event_id)
        asyncio.create_task(self._send_message_status(event_id, err=None))
        if response.ttl_period:
            await self._mark_disappearing(
                event_id=event_id,
                seconds=response.ttl_period,
                expires_at=int(response.date.timestamp()) + response.ttl_period,
            )

    async def _send_message_status(self, event_id: EventID, err: Exception | None) -> None:
        if not self.config["bridge.message_status_events"]:
            return
        intent = self.az.intent if self.encrypted else self.main_intent
        status = BeeperMessageStatusEventContent(
            network=self.bridge_info_state_key,
            relates_to=RelatesTo(
                rel_type=RelationType.REFERENCE,
                event_id=event_id,
            ),
        )
        if isinstance(err, IgnoredMessageError):
            status.reason = MessageStatusReason.UNSUPPORTED
            status.error = str(err)
            status.status = MessageStatus.FAIL
        elif err:
            status.reason = MessageStatusReason.GENERIC_ERROR
            status.error = str(err)
            status.status = MessageStatus.RETRIABLE
        else:
            status.status = MessageStatus.SUCCESS
        status.fill_legacy_booleans()

        await intent.send_message_event(
            room_id=self.mxid,
            event_type=EventType.BEEPER_MESSAGE_STATUS,
            content=status,
        )

    async def _send_bridge_error(
        self,
        sender: u.User,
        err: Exception,
        event_id: EventID,
        event_type: EventType,
        message_type: MessageType | None = None,
        msg: str | None = None,
    ) -> None:
        sender.send_remote_checkpoint(
            MessageSendCheckpointStatus.PERM_FAILURE,
            event_id,
            self.mxid,
            event_type,
            message_type=message_type,
            error=err,
        )

        if msg and self.config["bridge.delivery_error_reports"]:
            await self._send_message(
                self.main_intent, TextMessageEventContent(msgtype=MessageType.NOTICE, body=msg)
            )
        await self._send_message_status(event_id, err)

    async def handle_matrix_message(
        self, sender: u.User, content: MessageEventContent, event_id: EventID
    ) -> None:
        try:
            await self._handle_matrix_message(sender, content, event_id)
        except RPCError as e:
            self.log.exception(f"RPCError while bridging {event_id}: {e}")
            await self._send_bridge_error(
                sender,
                e,
                event_id,
                EventType.ROOM_MESSAGE,
                message_type=content.msgtype,
                msg=f"\u26a0 Your message may not have been bridged: {e}",
            )
            raise
        except Exception as e:
            if isinstance(e, IgnoredMessageError):
                self.log.debug(f"Ignored {event_id}: {e}")
            else:
                self.log.exception(f"Failed to bridge {event_id}")
            await self._send_bridge_error(
                sender,
                e,
                event_id,
                EventType.ROOM_MESSAGE,
                message_type=content.msgtype,
            )

    async def _find_source_msg(
        self, sender: u.User, content: MessageEventContent
    ) -> DBMessage | None:
        try:
            source = content["fi.mau.telegram.source"]
        except KeyError:
            return None
        if not isinstance(source, dict):
            return None
        try:
            msg_id = source["id"]
            space = source["space"]
            chat_id = source["chat_id"]
            peer_type = source["peer_type"]
        except KeyError:
            return None
        if (
            not isinstance(msg_id, int)
            or not isinstance(chat_id, int)
            or not isinstance(space, int)
            or not isinstance(peer_type, str)
        ):
            return None
        elif await sender.needs_relaybot(self):
            return None
        if peer_type == "user" and space != sender.tgid:
            return
        dbm = await DBMessage.get_one_by_tgid(TelegramID(msg_id), TelegramID(space))
        if dbm and peer_type == "chat" and space != sender.tgid:
            dbm = DBMessage.get_by_mxid(dbm.mxid, dbm.mx_room, sender.tgid)
        return dbm

    async def _handle_matrix_forward(
        self,
        sender: u.User,
        msg: DBMessage,
        event_id: EventID,
        space: TelegramID,
        msgtype: MessageType,
    ) -> bool:
        source_portal = await Portal.get_by_mxid(msg.mx_room)
        if not source_portal:
            return False
        async with self.send_lock(sender.tgid):
            try:
                response = await sender.client.forward_messages(
                    self.peer,
                    messages=[msg.tgid],
                    from_peer=source_portal.peer,
                )
            except Exception as e:
                self.log.warning(
                    f"Failed to send {event_id} from {sender.mxid} as forward of {msg.tgid} "
                    f"from {source_portal.tgid}: {e}, falling back to normal message handling"
                )
                return False
            else:
                await self._mark_matrix_handled(
                    sender=sender,
                    sender_tgid=sender.tgid,
                    event_type=EventType.ROOM_MESSAGE,
                    event_id=event_id,
                    space=space,
                    edit_index=0,
                    response=response[0],
                    msgtype=msgtype,
                )
                return True

    async def _handle_matrix_message(
        self, sender: u.User, content: MessageEventContent, event_id: EventID
    ) -> None:
        if not content.msgtype:
            raise IgnoredMessageError("Message doesn't have a msgtype")
        elif not content.body:
            raise IgnoredMessageError("Message doesn't have a body")

        logged_in = not await sender.needs_relaybot(self)
        client = sender.client if logged_in else self.bot.client
        space = (
            self.tgid
            if self.peer_type == "channel"  # Channels have their own ID space
            else (sender.tgid if logged_in else self.bot.tgid)
        )
        source_msg = await self._find_source_msg(sender, content)
        if source_msg and await self._handle_matrix_forward(
            sender, source_msg, event_id, space, content.msgtype
        ):
            return
        reply_to = await formatter.matrix_reply_to_telegram(content, space, room_id=self.mxid)

        media = (
            MessageType.STICKER,
            MessageType.IMAGE,
            MessageType.FILE,
            MessageType.AUDIO,
            MessageType.VIDEO,
        )

        if content.msgtype == MessageType.NOTICE:
            bridge_notices = self.get_config("bridge_notices.default")
            excepted = sender.mxid in self.get_config("bridge_notices.exceptions")
            if not bridge_notices and not excepted:
                raise IgnoredMessageError("Notices are not configured to be bridged.")

        if content.msgtype in (MessageType.TEXT, MessageType.EMOTE, MessageType.NOTICE):
            await self._pre_process_matrix_message(sender, not logged_in, content)
            await self._handle_matrix_text(
                sender, logged_in, event_id, space, client, content, reply_to
            )
        elif content.msgtype == MessageType.LOCATION:
            await self._pre_process_matrix_message(sender, not logged_in, content)
            await self._handle_matrix_location(
                sender, logged_in, event_id, space, client, content, reply_to
            )
        elif content.msgtype in media:
            file_name = content.body
            try:
                caption_content: TextMessageEventContent | None = sender.command_status["caption"]
                reply_to = reply_to or await formatter.matrix_reply_to_telegram(
                    caption_content, space, room_id=self.mxid
                )
                sender.command_status = None
            except (KeyError, TypeError):
                if not logged_in or (
                    "filename" in content and content["filename"] != content.body
                ):
                    if "filename" in content:
                        file_name = content["filename"]
                    caption_content = TextMessageEventContent(
                        msgtype=MessageType.TEXT,
                        body=content.body,
                    )
                    if (
                        "formatted_body" in content
                        and str(content.get("format")) == Format.HTML.value
                    ):
                        caption_content["formatted_body"] = content["formatted_body"]
                        caption_content["format"] = Format.HTML
                else:
                    caption_content = None
            if caption_content:
                caption_content.msgtype = content.msgtype
                await self._pre_process_matrix_message(sender, not logged_in, caption_content)
            await self._handle_matrix_file(
                sender,
                logged_in,
                event_id,
                space,
                client,
                content,
                reply_to,
                file_name,
                caption_content,
            )
        else:
            self.log.debug(
                f"Didn't handle Matrix event {event_id} due to unknown msgtype {content.msgtype}"
            )
            self.log.trace("Unhandled Matrix event content: %s", content)
            raise IgnoredMessageError(f"Unhandled msgtype {content.msgtype}")

    async def handle_matrix_unpin_all(self, sender: u.User, pin_event_id: EventID) -> None:
        await sender.client(UnpinAllMessagesRequest(peer=self.peer))
        await self._send_delivery_receipt(pin_event_id)

    async def handle_matrix_pin(
        self, sender: u.User, changes: dict[EventID, bool], pin_event_id: EventID
    ) -> None:
        tg_space = self.tgid if self.peer_type == "channel" else sender.tgid
        ids = {
            msg.mxid: msg.tgid
            for msg in await DBMessage.get_by_mxids(
                list(changes.keys()), mx_room=self.mxid, tg_space=tg_space
            )
        }
        for event_id, pinned in changes.items():
            try:
                await sender.client(
                    UpdatePinnedMessageRequest(peer=self.peer, id=ids[event_id], unpin=not pinned)
                )
            except (ChatNotModifiedError, MessageIdInvalidError, KeyError):
                pass
        await self._send_delivery_receipt(pin_event_id)

    async def handle_matrix_deletion(
        self, deleter: u.User, event_id: EventID, redaction_event_id: EventID
    ) -> None:
        try:
            await self._handle_matrix_deletion(deleter, event_id)
        except IgnoredMessageError as e:
            self.log.debug(str(e))
            await self._send_bridge_error(deleter, e, redaction_event_id, EventType.ROOM_REDACTION)
        except Exception as e:
            self.log.exception(f"Failed to bridge redaction by {deleter.mxid}")
            await self._send_bridge_error(deleter, e, redaction_event_id, EventType.ROOM_REDACTION)
        else:
            deleter.send_remote_checkpoint(
                MessageSendCheckpointStatus.SUCCESS,
                redaction_event_id,
                self.mxid,
                EventType.ROOM_REDACTION,
            )
            await self._send_delivery_receipt(redaction_event_id)
            asyncio.create_task(self._send_message_status(redaction_event_id, err=None))

    async def _handle_matrix_reaction_deletion(
        self, deleter: u.User, event_id: EventID, tg_space: TelegramID
    ) -> None:
        reaction = await DBReaction.get_by_mxid(event_id, self.mxid)
        if not reaction:
            raise IgnoredMessageError(f"Ignoring Matrix redaction of unknown event {event_id}")
        elif reaction.tg_sender != deleter.tgid:
            raise IgnoredMessageError(f"Ignoring Matrix redaction of reaction by another user")
        reaction_target = await DBMessage.get_by_mxid(
            reaction.msg_mxid, reaction.mx_room, tg_space
        )
        if not reaction_target or reaction_target.redacted:
            raise IgnoredMessageError(
                f"Ignoring Matrix redaction of reaction to unknown event {reaction.msg_mxid}"
            )
        async with self.reaction_lock(reaction_target.mxid):
            await reaction.delete()
            await deleter.client(SendReactionRequest(peer=self.peer, msg_id=reaction_target.tgid))

    async def _handle_matrix_deletion(self, deleter: u.User, event_id: EventID) -> None:
        real_deleter = deleter if not await deleter.needs_relaybot(self) else self.bot
        tg_space = self.tgid if self.peer_type == "channel" else real_deleter.tgid
        message = await DBMessage.get_by_mxid(event_id, self.mxid, tg_space)
        if not message:
            await self._handle_matrix_reaction_deletion(real_deleter, event_id, tg_space)
        elif message.redacted:
            raise IgnoredMessageError(
                "Ignoring Matrix redaction of already redacted event "
                f"{message.mxid} in {message.mx_room}"
            )
        elif message.edit_index != 0:
            await message.mark_redacted()
            raise IgnoredMessageError(
                f"Ignoring Matrix redaction of edit event {message.mxid} in {message.mx_room}"
            )
        else:
            await message.mark_redacted()
            await real_deleter.client.delete_messages(self.peer, [message.tgid])

    async def handle_matrix_reaction(
        self, user: u.User, target_event_id: EventID, emoji: str, reaction_event_id: EventID
    ) -> None:
        emoji_id = emoji
        reaction = ReactionEmoji(emoticon=variation_selector.remove(emoji))
        if emoji.startswith("mxc://"):
            db_reaction = await DBTelegramFile.find_by_mxc(ContentURI(emoji))
            if not db_reaction or not db_reaction.id.isdecimal():
                self.log.debug(f"Dropping unknown reaction {emoji} by {user.mxid}")
                if not self.has_bot:
                    await self.main_intent.redact(
                        self.mxid, reaction_event_id, reason="Unrecognized custom emoji"
                    )
                await self._send_bridge_error(
                    user,
                    Exception("Unrecognized custom emoji"),
                    reaction_event_id,
                    EventType.REACTION,
                )
                return
            reaction = ReactionCustomEmoji(document_id=int(db_reaction.id))
            emoji_id = db_reaction.id
        try:
            async with self.reaction_lock(target_event_id):
                await self._handle_matrix_reaction(
                    user, target_event_id, emoji_id, reaction, reaction_event_id
                )
        except IgnoredMessageError as e:
            self.log.debug(str(e))
            await self._send_bridge_error(user, e, reaction_event_id, EventType.REACTION)
        except ReactionInvalidError as e:
            # Don't redact reactions in relaybot chats, there are usually other Matrix users too.
            if not self.has_bot:
                await self.main_intent.redact(
                    self.mxid, reaction_event_id, reason="Emoji not allowed"
                )
            self.log.debug(f"Failed to bridge reaction by {user.mxid}: emoji not allowed")
            await self._send_bridge_error(user, e, reaction_event_id, EventType.REACTION)
        except Exception as e:
            self.log.exception(f"Failed to bridge reaction by {user.mxid}")
            await self._send_bridge_error(user, e, reaction_event_id, EventType.REACTION)
        else:
            user.send_remote_checkpoint(
                MessageSendCheckpointStatus.SUCCESS,
                reaction_event_id,
                self.mxid,
                EventType.REACTION,
            )
            await self._send_delivery_receipt(reaction_event_id)
            asyncio.create_task(self._send_message_status(reaction_event_id, err=None))

    async def _handle_matrix_reaction(
        self,
        user: u.User,
        target_event_id: EventID,
        emoji_id: str,
        reaction: TypeReaction,
        reaction_event_id: EventID,
    ) -> None:
        tg_space = self.tgid if self.peer_type == "channel" else user.tgid
        msg = await DBMessage.get_by_mxid(target_event_id, self.mxid, tg_space)
        if not msg:
            raise IgnoredMessageError(
                f"Ignoring Matrix reaction to unknown event {target_event_id}"
            )
        elif msg.redacted:
            raise IgnoredMessageError(
                f"Ignoring Matrix reaction to redacted event {target_event_id}"
            )
        elif msg.edit_index != 0:
            raise IgnoredMessageError(f"Ignoring Matrix reaction to edit event {target_event_id}")

        existing_reacts = await DBReaction.get_by_sender(msg.mxid, msg.mx_room, user.tgid)
        new_tg_reactions: list[TypeReaction] = []
        reactions_to_remove: list[DBReaction] = []
        max_reactions = 3 if user.is_premium else 1
        max_reactions -= 1  # Leave one reaction of space for the new reaction
        for db_reaction in existing_reacts:
            if db_reaction.reaction == emoji_id:
                raise IgnoredMessageError("Ignoring duplicate Matrix reaction")
            if len(new_tg_reactions) < max_reactions:
                new_tg_reactions.append(db_reaction.telegram)
            else:
                reactions_to_remove.append(db_reaction)
        new_tg_reactions.append(reaction)

        await user.client(
            SendReactionRequest(peer=self.peer, msg_id=msg.tgid, reaction=new_tg_reactions)
        )
        puppet = await user.get_puppet()
        for db_reaction in reactions_to_remove:
            await db_reaction.delete()
            await puppet.intent_for(self).redact(db_reaction.mx_room, db_reaction.mxid)
        await DBReaction(
            mxid=reaction_event_id,
            mx_room=self.mxid,
            msg_mxid=msg.mxid,
            tg_sender=user.tgid,
            reaction=emoji_id,
        ).save()

    async def _update_telegram_power_level(
        self, sender: u.User, user_id: TelegramID, level: int
    ) -> None:
        moderator = level >= 50
        admin = level >= 75
        await sender.client.edit_admin(
            self.peer,
            user_id,
            change_info=moderator,
            post_messages=moderator,
            edit_messages=moderator,
            delete_messages=moderator,
            ban_users=moderator,
            invite_users=moderator,
            pin_messages=moderator,
            add_admins=admin,
        )

    async def handle_matrix_power_levels(
        self,
        sender: u.User,
        new_users: dict[UserID, int],
        old_users: dict[UserID, int],
        event_id: EventID | None,
    ) -> None:
        # TODO handle all power level changes and bridge exact admin rights to supergroups/channels
        for user, level in new_users.items():
            if not user or user == self.main_intent.mxid or user == sender.mxid:
                continue
            user_id = p.Puppet.get_id_from_mxid(user)
            if not user_id:
                mx_user = await u.User.get_by_mxid(user, create=False)
                if not mx_user or not mx_user.tgid:
                    continue
                user_id = mx_user.tgid
            if not user_id or user_id == sender.tgid:
                continue
            if user not in old_users or level != old_users[user]:
                await self._update_telegram_power_level(sender, user_id, level)

    async def handle_matrix_about(self, sender: u.User, about: str, event_id: EventID) -> None:
        if self.peer_type not in ("chat", "channel"):
            return
        peer = await self.get_input_entity(sender)
        await sender.client(EditChatAboutRequest(peer=peer, about=about))
        self.about = about
        await self.save()
        await self._send_delivery_receipt(event_id)

    async def handle_matrix_title(self, sender: u.User, title: str, event_id: EventID) -> None:
        if self.peer_type not in ("chat", "channel"):
            return

        if self.peer_type == "chat":
            response = await sender.client(EditChatTitleRequest(chat_id=self.tgid, title=title))
        else:
            channel = await self.get_input_entity(sender)
            response = await sender.client(EditTitleRequest(channel=channel, title=title))
        self.dedup.register_outgoing_actions(response)
        self.title = title
        await self.save()
        await self._send_delivery_receipt(event_id)
        await self.update_bridge_info()

    async def handle_matrix_avatar(
        self, sender: u.User, url: ContentURI, event_id: EventID
    ) -> None:
        if self.peer_type not in ("chat", "channel"):
            # Invalid peer type
            return
        elif self.avatar_url == url:
            return

        self.avatar_url = url
        file = await self.main_intent.download_media(url)
        mime = magic.mimetype(file)
        ext = sane_mimetypes.guess_extension(mime)
        uploaded = await sender.client.upload_file(file, file_name=f"avatar{ext}")
        photo = InputChatUploadedPhoto(file=uploaded)

        if self.peer_type == "chat":
            response = await sender.client(EditChatPhotoRequest(chat_id=self.tgid, photo=photo))
        else:
            channel = await self.get_input_entity(sender)
            response = await sender.client(EditPhotoRequest(channel=channel, photo=photo))
        self.dedup.register_outgoing_actions(response)
        for update in response.updates:
            is_photo_update = (
                isinstance(update, UpdateNewMessage)
                and isinstance(update.message, MessageService)
                and isinstance(update.message.action, MessageActionChatEditPhoto)
            )
            if is_photo_update:
                loc, size = self._msg_conv.get_largest_photo_size(update.message.action.photo)
                self.photo_id = str(loc.id)
                await self.save()
                break
        await self._send_delivery_receipt(event_id)
        await self.update_bridge_info()

    async def handle_matrix_upgrade(
        self, sender: UserID, new_room: RoomID, event_id: EventID
    ) -> None:
        _, server = self.main_intent.parse_user_id(sender)
        old_room = self.mxid
        await self.migrate_and_save_matrix(new_room)
        await self.main_intent.join_room(new_room, servers=[server])
        entity: TypeChat | User | None = None
        user: au.AbstractUser | None = None
        if self.bot and self.has_bot:
            user = self.bot
            entity = await self.get_entity(self.bot)
        if not entity:
            user_mxids = await self.main_intent.get_room_members(self.mxid)
            for user_str in user_mxids:
                user_id = UserID(user_str)
                if user_id == self.az.bot_mxid:
                    continue
                user = await u.User.get_by_mxid(user_id, create=False)
                if user and user.tgid:
                    entity = await self.get_entity(user)
                    if entity:
                        break
        if not entity:
            self.log.error(
                "Failed to fully migrate to upgraded Matrix room: no Telegram user found."
            )
            return
        await self.update_matrix_room(user, entity)
        self.log.info(f"{sender} upgraded room from {old_room} to {self.mxid}")
        await self._send_delivery_receipt(event_id, room_id=old_room)

    async def migrate_and_save_matrix(self, new_id: RoomID) -> None:
        try:
            del self.by_mxid[self.mxid]
        except KeyError:
            pass
        self.mxid = new_id
        self.next_batch_id = None
        self.first_event_id = None
        self.by_mxid[self.mxid] = self
        await self.save()

    # endregion
    # region Telegram -> Matrix bridging

    async def handle_telegram_typing(self, user: p.Puppet, update: UpdateTyping) -> None:
        if user.is_real_user:
            # Ignore typing notifications from double puppeted users to avoid echoing
            return
        is_typing = isinstance(update.action, SendMessageTypingAction)
        await user.default_mxid_intent.set_typing(self.mxid, is_typing=is_typing)

    async def handle_telegram_edit(
        self, source: au.AbstractUser, sender: p.Puppet | None, evt: Message
    ) -> None:
        if not self.mxid:
            self.log.trace("Ignoring edit to %d as chat has no Matrix room", evt.id)
            return
        elif hasattr(evt, "media") and isinstance(evt.media, MessageMediaGame):
            self.log.debug("Ignoring game message edit event")
            return

        if self.peer_type != "channel" and isinstance(evt, Message) and evt.reactions is not None:
            asyncio.create_task(
                self.try_handle_telegram_reactions(source, TelegramID(evt.id), evt.reactions)
            )
        sender_id = sender.tgid if sender else self.tgid

        async with self.send_lock(sender_id, required=False):
            tg_space = self.tgid if self.peer_type == "channel" else source.tgid

            temporary_identifier = EventID(
                f"${random.randint(1000000000000, 9999999999999)}TGBRIDGEDITEMP"
            )
            event_hash, duplicate_found = self.dedup.check(
                evt, (temporary_identifier, tg_space), force_hash=True
            )
            if duplicate_found:
                mxid, other_tg_space = duplicate_found
                if tg_space != other_tg_space:
                    prev_edit_msg = await DBMessage.get_one_by_tgid(
                        TelegramID(evt.id), tg_space, edit_index=-1
                    )
                    if (
                        not prev_edit_msg
                        or prev_edit_msg.mxid == mxid
                        or prev_edit_msg.content_hash == event_hash
                    ):
                        return
                    await DBMessage(
                        mxid=mxid,
                        mx_room=self.mxid,
                        tg_space=tg_space,
                        tgid=TelegramID(evt.id),
                        edit_index=prev_edit_msg.edit_index + 1,
                        content_hash=event_hash,
                        sender=sender_id,
                    ).insert()
                return

        editing_msg = await DBMessage.get_one_by_tgid(TelegramID(evt.id), tg_space)
        if not editing_msg:
            self.log.info(
                f"Didn't find edited message {evt.id}@{tg_space} (src {source.tgid}) "
                "in database."
            )
            return
        prev_edit_msg = (
            await DBMessage.get_one_by_tgid(TelegramID(evt.id), tg_space, -1) or editing_msg
        )
        if prev_edit_msg.content_hash == event_hash:
            self.log.debug(
                f"Ignoring edit of message {evt.id}@{tg_space} (src {source.tgid}):"
                " content hash didn't change"
            )
            await DBMessage.delete_temp_mxid(temporary_identifier, self.mxid)
            return

        intent = sender.intent_for(self) if sender else self.main_intent
        is_bot = sender.is_bot if sender else False
        converted = await self._msg_conv.convert(
            source, intent, is_bot, evt, no_reply_fallback=True
        )
        converted.content.set_edit(editing_msg.mxid)
        await intent.set_typing(self.mxid, is_typing=False)
        timestamp = evt.edit_date if evt.edit_date != evt.date else None
        event_id = await self._send_message(
            intent, converted.content, timestamp=timestamp, event_type=converted.type
        )

        await DBMessage(
            mxid=event_id,
            mx_room=self.mxid,
            tg_space=tg_space,
            tgid=TelegramID(evt.id),
            edit_index=prev_edit_msg.edit_index + 1,
            content_hash=event_hash,
            sender=sender_id,
        ).insert()
        await DBMessage.replace_temp_mxid(temporary_identifier, self.mxid, event_id)

    @property
    def _takeout_options(self) -> dict[str, bool | int]:
        return {
            "files": True,
            "megagroups": self.megagroup,
            "chats": self.peer_type == "chat",
            "users": self.peer_type == "user",
            "channels": (self.peer_type == "channel" and not self.megagroup),
            "max_file_size": min(self.matrix.media_config.upload_size, 2000 * 1024 * 1024),
        }

    async def backfill(
        self,
        source: u.User,
        is_initial: bool = False,
        limit: int | None = None,
        last_id: int | None = None,
    ) -> None:
        async with self.backfill_method_lock:
            await self._locked_backfill(source, is_initial, limit, last_id)

    async def _locked_backfill(
        self,
        source: u.User,
        is_initial: bool = False,
        limit: int | None = None,
        last_tgid: int | None = None,
    ) -> None:
        limit = limit or (
            self.config["bridge.backfill.initial_limit"]
            if is_initial
            else self.config["bridge.backfill.missed_limit"]
        )
        if limit == 0:
            return
        if not self.config["bridge.backfill.normal_groups"] and self.peer_type == "chat":
            return
        last_in_room = await DBMessage.find_last(
            self.mxid, (source.tgid if self.peer_type != "channel" else self.tgid)
        )
        min_id = last_in_room.tgid if last_in_room else 0
        if last_tgid is None:
            messages = await source.client.get_messages(self.peer, limit=1)
            if not messages:
                # The chat seems empty
                return
            last_tgid = messages[0].id
        if last_tgid <= min_id or (last_tgid == 1 and self.peer_type == "channel"):
            # Nothing to backfill
            return
        if limit < 0:
            limit = last_tgid - min_id
            limit_type = "unlimited"
        elif self.peer_type == "channel":
            min_id = max(last_tgid - limit, min_id)
            # This is now just an approximate message count, not the actual limit.
            limit = last_tgid - min_id
            limit_type = "channel"
        else:
            # This limit will be higher than the actual message count if there are any messages
            # in other DMs or normal groups, but that's not too bad.
            limit = min(last_tgid - min_id, limit)
            limit_type = "dm/minigroup"
        self.log.debug(
            f"Backfilling up to {limit} messages after ID {min_id} through {source.mxid} "
            f"(last message: {last_tgid}, limit type: {limit_type})"
        )
        with self.backfill_lock:
            await self._backfill(source, min_id, limit)

    async def _backfill(self, source: u.User, min_id: int, limit: int) -> None:
        self.backfill_leave = set()
        if (
            self.peer_type == "user"
            and self.tgid != source.tgid
            and self.config["bridge.backfill.invite_own_puppet"]
        ):
            self.log.debug("Adding %s's default puppet to room for backfilling", source.mxid)
            sender = await p.Puppet.get_by_tgid(source.tgid)
            await self.main_intent.invite_user(self.mxid, sender.default_mxid)
            await sender.default_mxid_intent.join_room_by_id(self.mxid)
            self.backfill_leave.add(sender.default_mxid_intent)

        client = source.client
        async with NotificationDisabler(self.mxid, source):
            if limit > self.config["bridge.backfill.takeout_limit"]:
                self.log.debug(f"Opening takeout client for {source.tgid}")
                async with client.takeout(**self._takeout_options) as takeout:
                    count, handled = await self._backfill_messages(source, min_id, limit, takeout)
            else:
                count, handled = await self._backfill_messages(source, min_id, limit, client)

        for intent in self.backfill_leave:
            self.log.trace("Leaving room with %s post-backfill", intent.mxid)
            await intent.leave_room(self.mxid)
        self.backfill_leave = None
        self.log.info(
            "Backfilled %d (of %d fetched) messages through %s", handled, count, source.mxid
        )

    async def _backfill_messages(
        self, source: u.User, min_id: int, limit: int, client: MautrixTelegramClient
    ) -> tuple[int, int]:
        count = handled_count = 0
        entity = await self.get_input_entity(source)
        if self.peer_type == "channel":
            # This is a channel or supergroup, so we'll backfill messages based on the ID.
            # There are some cases, such as deleted messages, where this may backfill less
            # messages than the limit.
            self.log.debug(f"Iterating all messages starting with {min_id} (approx: {limit})")
            messages = client.iter_messages(entity, reverse=True, min_id=min_id)
            async for message in messages:
                count += 1
                was_handled = await self._handle_telegram_backfill_message(source, message)
                handled_count += 1 if was_handled else 0
        else:
            # Private chats and normal groups don't have their own message ID namespace,
            # which means we'll have to fetch messages a different way.
            self.log.debug(
                f"Fetching up to {limit} most recent messages, ignoring anything before {min_id}"
            )
            messages = await client.get_messages(entity, min_id=min_id, limit=limit)
            for message in reversed(messages):
                count += 1
                if message.id <= min_id:
                    self.log.trace(
                        f"Skipping {message.id} in backfill response as it's lower than "
                        f"the last bridged message ({min_id})"
                    )
                    continue
                was_handled = await self._handle_telegram_backfill_message(source, message)
                handled_count += 1 if was_handled else 0
        return count, handled_count

    async def _handle_telegram_backfill_message(
        self, source: au.AbstractUser, msg: Message | MessageService
    ) -> bool:
        if msg.from_id and isinstance(msg.from_id, (PeerUser, PeerChannel)):
            sender = await p.Puppet.get_by_peer(msg.from_id)
        elif isinstance(msg.peer_id, PeerUser):
            if msg.out:
                sender = await p.Puppet.get_by_tgid(source.tgid)
            else:
                sender = await p.Puppet.get_by_peer(msg.peer_id)
        else:
            sender = None
        if isinstance(msg, MessageService):
            if isinstance(msg.action, MessageActionContactSignUp):
                await self.handle_telegram_joined(source, sender, msg, backfill=True)
                return True
            else:
                self.log.debug(
                    f"Unhandled service message {type(msg.action).__name__} in backfill"
                )
        elif isinstance(msg, Message):
            await self.handle_telegram_message(source, sender, msg)
            return True
        else:
            self.log.debug(f"Unhandled message type {type(msg).__name__} in backfill")
        return False

    def _split_dm_reaction_counts(self, counts: list[ReactionCount]) -> list[MessagePeerReaction]:
        reactions = []
        for item in counts:
            if item.count == 2:
                reactions += [
                    MessagePeerReaction(reaction=item.reaction, peer_id=PeerUser(self.tgid)),
                    MessagePeerReaction(
                        reaction=item.reaction, peer_id=PeerUser(self.tg_receiver)
                    ),
                ]
            elif item.count == 1:
                reactions.append(
                    MessagePeerReaction(
                        reaction=item.reaction,
                        peer_id=PeerUser(self.tg_receiver if item.chosen_order else self.tgid),
                    )
                )
        return reactions

    async def _poll_telegram_reactions(self, source: au.AbstractUser) -> None:
        now = time.monotonic()
        if self._prev_reaction_poll[source.mxid] + REACTION_POLL_MIN_INTERVAL > now:
            self.log.trace(
                f"Not polling reactions through {source.mxid}, "
                f"last poll was less than {REACTION_POLL_MIN_INTERVAL} seconds ago"
            )
            return
        self._prev_reaction_poll[source.mxid] = now
        self.log.debug(f"Polling reactions for recent messages through {source.mxid}")
        messages = await DBMessage.find_recent(self.mxid, source.tgid)
        message_ids = [message.tgid for message in messages]
        updates = await source.client(GetMessagesReactionsRequest(peer=self.peer, id=message_ids))
        for user in updates.users:
            user: User
            puppet = await p.Puppet.get_by_tgid(TelegramID(user.id))
            await puppet.update_info(source, user)
        for upd in updates.updates:
            if isinstance(upd, UpdateMessageReactions):
                await self.handle_telegram_reactions(source, TelegramID(upd.msg_id), upd.reactions)
            else:
                self.log.warning(f"Unexpected update type {type(upd)} in get reactions response")

    async def try_handle_telegram_reactions(
        self,
        source: au.AbstractUser,
        msg_id: TelegramID,
        data: MessageReactions,
        dbm: DBMessage | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        try:
            await self.handle_telegram_reactions(source, msg_id, data, dbm, timestamp)
        except Exception:
            self.log.exception(f"Error handling reactions in message {msg_id}")

    async def handle_telegram_reactions(
        self,
        source: au.AbstractUser,
        msg_id: TelegramID,
        data: MessageReactions,
        dbm: DBMessage | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        total_count = sum(item.count for item in data.results)
        recent_reactions = data.recent_reactions or []
        if total_count > 0 and not recent_reactions and not data.can_see_list:
            # We don't know who reacted in a channel, so we can't bridge it properly either
            return
        if self.peer_type == "channel" and not self.megagroup:
            # This should never happen with the previous if
            self.log.warning(f"Can see reaction list in channel ({data!s})")
            # return

        tg_space = self.tgid if self.peer_type == "channel" else source.tgid
        if dbm is None:
            dbm = await DBMessage.get_one_by_tgid(msg_id, tg_space)
            if dbm is None:
                return

        if not recent_reactions or len(recent_reactions) < total_count:
            if self.peer_type == "user":
                recent_reactions = self._split_dm_reaction_counts(data.results)
            elif source.is_bot:
                # Can't fetch exact reaction senders as a bot
                return
            else:
                # TODO should calls to this be limited?
                resp = await source.client(
                    GetMessageReactionsListRequest(peer=self.peer, id=dbm.tgid, limit=100)
                )
                recent_reactions = resp.reactions

        async with self.reaction_lock(dbm.mxid):
            await self._handle_telegram_reactions_locked(
                source, dbm, recent_reactions, total_count, timestamp=timestamp
            )

    @staticmethod
    def _reactions_filter(lst: list[TypeReaction], existing: DBReaction) -> bool:
        if not lst:
            return False
        for reaction in lst:
            if isinstance(reaction, ReactionCustomEmoji) and existing.reaction == str(
                reaction.document_id
            ):
                lst.remove(reaction)
                return True
            elif isinstance(reaction, ReactionEmoji) and existing.reaction == reaction.emoticon:
                lst.remove(reaction)
                return True
        return False

    @staticmethod
    async def _get_reaction_limit(sender: TelegramID) -> int:
        puppet = await p.Puppet.get_by_tgid(sender, create=False)
        if puppet and puppet.is_premium:
            return 3
        return 1

    async def _handle_telegram_reactions_locked(
        self,
        source: au.AbstractUser,
        msg: DBMessage,
        reaction_list: list[MessagePeerReaction],
        total_count: int,
        timestamp: datetime | None = None,
    ) -> None:
        reactions: dict[TelegramID, list[TypeReaction]] = {}
        custom_emoji_ids: list[int] = []
        for reaction in reaction_list:
            if isinstance(reaction.peer_id, (PeerUser, PeerChannel)) and isinstance(
                reaction.reaction, (ReactionEmoji, ReactionCustomEmoji)
            ):
                reactions.setdefault(p.Puppet.get_id_from_peer(reaction.peer_id), []).append(
                    reaction.reaction
                )
                if isinstance(reaction.reaction, ReactionCustomEmoji):
                    custom_emoji_ids.append(reaction.reaction.document_id)
        is_full = len(reaction_list) == total_count
        custom_emojis = await util.transfer_custom_emojis_to_matrix(source, custom_emoji_ids)

        existing_reactions = await DBReaction.get_all_by_message(msg.mxid, msg.mx_room)

        removed: list[DBReaction] = []
        for existing_reaction in existing_reactions:
            sender_id = existing_reaction.tg_sender
            new_reactions = reactions.get(sender_id)
            if self._reactions_filter(new_reactions, existing_reaction):
                if new_reactions is not None and len(new_reactions) == 0:
                    reactions.pop(sender_id)
            else:
                if is_full or (
                    new_reactions is not None
                    and len(new_reactions) == await self._get_reaction_limit(sender_id)
                ):
                    removed.append(existing_reaction)
                # else: assume the reaction is still there, too much effort to fetch it

        new_reaction: TypeReaction
        for sender, new_reactions in reactions.items():
            for new_reaction in new_reactions:
                if isinstance(new_reaction, ReactionEmoji):
                    emoji_id = new_reaction.emoticon
                    matrix_reaction = variation_selector.add(new_reaction.emoticon)
                elif isinstance(new_reaction, ReactionCustomEmoji):
                    emoji_id = str(new_reaction.document_id)
                    matrix_reaction = custom_emojis[new_reaction.document_id].mxc
                else:
                    self.log.warning("Unknown reaction type %s", type(new_reaction))
                    continue
                self.log.debug(f"Bridging reaction {emoji_id} by {sender} to {msg.tgid}")
                puppet: p.Puppet = await p.Puppet.get_by_tgid(sender)
                mxid = await puppet.intent_for(self).react(
                    msg.mx_room, msg.mxid, matrix_reaction, timestamp=timestamp
                )
                await DBReaction(
                    mxid=mxid,
                    mx_room=msg.mx_room,
                    msg_mxid=msg.mxid,
                    tg_sender=sender,
                    reaction=emoji_id,
                ).save()
        for removed_reaction in removed:
            self.log.debug(
                f"Removing reaction {removed_reaction.reaction} by {removed_reaction.tg_sender} "
                f"to {msg.tgid}"
            )
            puppet = await p.Puppet.get_by_tgid(removed_reaction.tg_sender)
            await puppet.intent_for(self).redact(removed_reaction.mx_room, removed_reaction.mxid)
            await removed_reaction.delete()

    async def handle_telegram_message(
        self, source: au.AbstractUser, sender: p.Puppet | None, evt: Message
    ) -> None:
        if not self.mxid:
            self.log.trace("Got telegram message %d, but no room exists, creating...", evt.id)
            await self.create_matrix_room(source, invites=[source.mxid], update_if_exists=False)
            if not self.mxid:
                self.log.warning("Room doesn't exist even after creating, dropping %d", evt.id)
                return

        if (
            self.peer_type == "user"
            and sender
            and sender.tgid == self.tg_receiver
            and not sender.is_real_user
            and not await self.az.state_store.is_joined(self.mxid, sender.mxid)
        ):
            self.log.debug(
                f"Ignoring private chat message {evt.id}@{source.tgid} as receiver does"
                " not have matrix puppeting and their default puppet isn't in the room"
            )
            return

        sender_id = sender.tgid if sender else self.tgid
        async with self.send_lock(sender_id, required=False):
            tg_space = self.tgid if self.peer_type == "channel" else source.tgid

            temporary_identifier = EventID(
                f"${random.randint(1000000000000, 9999999999999)}TGBRIDGETEMP"
            )
            event_hash, duplicate_found = self.dedup.check(evt, (temporary_identifier, tg_space))
            if duplicate_found:
                mxid, other_tg_space = duplicate_found
                self.log.debug(
                    f"Ignoring message {evt.id}@{tg_space} (src {source.tgid}) "
                    f"as it was already handled (in space {other_tg_space})"
                )
                if tg_space != other_tg_space:
                    await DBMessage(
                        tgid=TelegramID(evt.id),
                        mx_room=self.mxid,
                        mxid=mxid,
                        tg_space=tg_space,
                        edit_index=0,
                        content_hash=event_hash,
                        sender=sender_id,
                    ).insert()
                return

        msg = await DBMessage.get_one_by_tgid(TelegramID(evt.id), tg_space)
        if msg:
            self.log.debug(
                f"Ignoring message {evt.id} (src {source.tgid}) as it was already "
                f"handled into {msg.mxid}."
            )
            return

        self.log.debug(
            "Handling Telegram message %d@%d from %s (ts: %s)",
            evt.id,
            tg_space,
            sender_id,
            evt.date,
        )
        self.log.trace("Message content: %s", evt)

        if sender and not sender.displayname:
            self.log.debug(
                f"Telegram user {sender.tgid} sent a message, but doesn't have a displayname,"
                " updating info..."
            )
            entity = await source.client.get_entity(sender.peer)
            await sender.update_info(source, entity)
            if not sender.displayname:
                self.log.debug(
                    f"Telegram user {sender.tgid} doesn't have a displayname even after"
                    f" updating with data {entity!s}"
                )

        if sender:
            # TODO don't use double puppet when backfilling
            intent = sender.intent_for(self)
        else:
            intent = self.main_intent
        is_bot = sender.is_bot if sender else False
        converted = await self._msg_conv.convert(source, intent, is_bot, evt)
        if not converted:
            return
        await intent.set_typing(self.mxid, is_typing=False)
        event_id = await self._send_message(
            intent, converted.content, timestamp=evt.date, event_type=converted.type
        )
        caption_id = None
        if converted.caption:
            caption_id = await self._send_message(intent, converted.caption, timestamp=evt.date)

        self._new_messages_after_sponsored = True

        another_event_hash, prev_id = self.dedup.update(
            evt, (event_id, tg_space), (temporary_identifier, tg_space)
        )
        assert another_event_hash == event_hash
        if prev_id:
            self.log.debug(
                f"Sent message {evt.id}@{tg_space} to Matrix as {event_id}. "
                f"Temporary dedup identifier was {temporary_identifier}, "
                f"but dedup map contained {prev_id[1]} instead! -- "
                "This was probably a race condition caused by Telegram sending updates"
                "to other clients before responding to the sender. I'll just redact "
                "the likely duplicate message now."
            )
            await intent.redact(self.mxid, event_id)
            return

        self.log.debug("Handled Telegram message %d@%d -> %s", evt.id, tg_space, event_id)
        try:
            dbm = DBMessage(
                tgid=TelegramID(evt.id),
                mx_room=self.mxid,
                mxid=event_id,
                tg_space=tg_space,
                edit_index=0,
                content_hash=event_hash,
                sender=sender_id,
            )
            await dbm.insert()
            await DBMessage.replace_temp_mxid(temporary_identifier, self.mxid, event_id)
        except (IntegrityError, UniqueViolationError) as e:
            self.log.error(
                f"{type(e).__name__} while saving message mapping {evt.id}@{tg_space} "
                f"-> {event_id}: {e}"
            )
            await intent.redact(self.mxid, event_id)
            return
        if isinstance(evt, Message) and evt.reactions:
            asyncio.create_task(
                self.try_handle_telegram_reactions(
                    source, dbm.tgid, evt.reactions, dbm=dbm, timestamp=evt.date
                )
            )
        await self._send_delivery_receipt(event_id)
        if converted.disappear_seconds:
            if converted.disappear_start_immediately:
                expires_at = int(evt.date.timestamp()) + converted.disappear_seconds
            else:
                expires_at = None
            await self._mark_disappearing(event_id, converted.disappear_seconds, expires_at)
            if caption_id:
                await self._mark_disappearing(caption_id, converted.disappear_seconds, expires_at)

    async def _mark_disappearing(
        self, event_id: EventID, seconds: int, expires_at: int | None
    ) -> None:
        dm = DisappearingMessage(self.mxid, event_id, seconds, expiration_ts=expires_at * 1000)
        await dm.insert()
        if expires_at:
            asyncio.create_task(self._disappear_event(dm))

    async def _create_room_on_action(
        self, source: au.AbstractUser, action: TypeMessageAction
    ) -> bool:
        if source.is_relaybot and self.config["bridge.ignore_unbridged_group_chat"]:
            return False
        create_and_exit = (MessageActionChatCreate, MessageActionChannelCreate)
        create_and_continue = (
            MessageActionChatAddUser,
            MessageActionChatJoinedByLink,
            MessageActionChatJoinedByRequest,
        )
        if isinstance(action, create_and_exit) or isinstance(action, create_and_continue):
            await self.create_matrix_room(
                source, invites=[source.mxid], update_if_exists=isinstance(action, create_and_exit)
            )
        if not isinstance(action, create_and_continue):
            return False
        return True

    async def handle_telegram_action(
        self, source: au.AbstractUser, sender: p.Puppet | None, update: MessageService
    ) -> None:
        action = update.action
        should_ignore = (
            not self.mxid and not await self._create_room_on_action(source, action)
        ) or self.dedup.check_action(update)
        if should_ignore or not self.mxid:
            return
        if isinstance(action, MessageActionChatEditTitle):
            await self._update_title(action.title, sender=sender, save=True)
            await self.update_bridge_info()
        elif isinstance(action, MessageActionChatEditPhoto):
            await self._update_avatar(source, action.photo, sender=sender, save=True)
            await self.update_bridge_info()
        elif isinstance(action, MessageActionChatDeletePhoto):
            await self._update_avatar(source, ChatPhotoEmpty(), sender=sender, save=True)
            await self.update_bridge_info()
        elif isinstance(action, MessageActionChatAddUser):
            for user_id in action.users:
                await self._add_telegram_user(TelegramID(user_id), source)
        elif isinstance(action, (MessageActionChatJoinedByLink, MessageActionChatJoinedByRequest)):
            await self._add_telegram_user(sender.id, source)
        elif isinstance(action, MessageActionChatDeleteUser):
            await self.delete_telegram_user(TelegramID(action.user_id), sender)
        elif isinstance(action, MessageActionChatMigrateTo):
            await self._migrate_and_save_telegram(TelegramID(action.channel_id))
            # TODO encrypt
            await sender.intent_for(self).send_emote(
                self.mxid, "upgraded this group to a supergroup."
            )
            await self.update_bridge_info()
        elif isinstance(action, MessageActionGameScore):
            # TODO handle game score
            pass
        elif isinstance(action, MessageActionContactSignUp):
            await self.handle_telegram_joined(source, sender, update)
        else:
            self.log.trace("Unhandled Telegram action in %s: %s", self.title, action)

    async def handle_telegram_joined(
        self,
        source: au.AbstractUser,
        sender: p.Puppet,
        update: MessageService,
        backfill: bool = False,
    ) -> None:
        assert isinstance(update.action, MessageActionContactSignUp)

        msg = await DBMessage.get_one_by_tgid(TelegramID(update.id), source.tgid)
        if msg:
            self.log.debug(
                f"Ignoring new user message {update.id} (src {source.tgid}) as it was already "
                f"handled into {msg.mxid}."
            )
            return

        content = TextMessageEventContent(msgtype=MessageType.EMOTE, body="joined Telegram")
        event_id = await self._send_message(
            sender.intent_for(self), content, timestamp=update.date
        )
        await DBMessage(
            tgid=TelegramID(update.id),
            mx_room=self.mxid,
            mxid=event_id,
            tg_space=source.tgid,
            edit_index=0,
            sender=sender.id,
        ).insert()
        if self.config["bridge.always_read_joined_telegram_notice"]:
            double_puppet = await p.Puppet.get_by_tgid(source.tgid)
            if double_puppet and double_puppet.is_real_user:
                await double_puppet.intent.mark_read(self.mxid, event_id)

    async def set_telegram_admin(self, user_id: TelegramID) -> None:
        puppet = await p.Puppet.get_by_tgid(user_id)
        user = await u.User.get_by_tgid(user_id)

        levels = await self.main_intent.get_power_levels(self.mxid)
        if user:
            levels.users[user.mxid] = 50
        if puppet:
            levels.users[puppet.mxid] = 50
        await self.main_intent.set_power_levels(self.mxid, levels)

    async def receive_telegram_pin_ids(
        self, msg_ids: list[TelegramID], receiver: TelegramID, remove: bool
    ) -> None:
        async with self._pin_lock:
            tg_space = receiver if self.peer_type != "channel" else self.tgid
            previously_pinned = await self.main_intent.get_pinned_messages(self.mxid)
            currently_pinned_dict = {event_id: True for event_id in previously_pinned}
            for message in await DBMessage.get_first_by_tgids(msg_ids, tg_space):
                if remove:
                    currently_pinned_dict.pop(message.mxid, None)
                else:
                    currently_pinned_dict[message.mxid] = True
            currently_pinned = list(currently_pinned_dict.keys())
            if currently_pinned != previously_pinned:
                await self.main_intent.set_pinned_messages(self.mxid, currently_pinned)

    async def set_telegram_admins_enabled(self, enabled: bool) -> None:
        level = 50 if enabled else 10
        levels = await self.main_intent.get_power_levels(self.mxid)
        levels.invite = level
        levels.events[EventType.ROOM_NAME] = level
        levels.events[EventType.ROOM_AVATAR] = level
        await self.main_intent.set_power_levels(self.mxid, levels)

    # endregion
    # region Miscellaneous getters

    def get_config(self, key: str) -> Any:
        local = util.recursive_get(self.local_config, key)
        if local is not None:
            return local
        return self.config[f"bridge.{key}"]

    async def can_user_perform(self, user: u.User, event: str) -> bool:
        if user.is_admin:
            return True
        if not self.mxid:
            # No room for anybody to perform actions in
            return False
        try:
            await self.main_intent.get_power_levels(self.mxid)
        except MatrixRequestError:
            return False
        evt_type = EventType.find(f"fi.mau.telegram.{event}", t_class=EventType.Class.STATE)
        return await self.main_intent.state_store.has_power_level(self.mxid, user.mxid, evt_type)

    def get_input_entity(
        self, user: au.AbstractUser
    ) -> Awaitable[TypeInputPeer | TypeInputChannel]:
        return user.client.get_input_entity(self.peer)

    async def get_entity(self, user: au.AbstractUser) -> TypeChat:
        try:
            return await user.client.get_entity(self.peer)
        except ValueError:
            if user.is_bot:
                self.log.warning(f"Could not find entity with bot {user.tgid}. Failing...")
                raise
            self.log.warning(
                f"Could not find entity with user {user.tgid}. falling back to get_dialogs."
            )
            async for dialog in user.client.iter_dialogs():
                if dialog.entity.id == self.tgid:
                    return dialog.entity
            raise

    async def get_invite_link(
        self,
        user: u.User,
        uses: int | None = None,
        expire: datetime | None = None,
        request_needed: bool = False,
        title: str | None = None,
    ) -> str:
        if self.peer_type == "user":
            raise ValueError("You can't invite users to private chats.")
        if self.username:
            return f"https://t.me/{self.username}"
        link = await user.client(
            ExportChatInviteRequest(
                peer=await self.get_input_entity(user),
                expire_date=expire,
                usage_limit=uses,
                request_needed=request_needed,
                title=title,
            )
        )
        return link.link

    # endregion
    # region Matrix room cleanup

    async def get_authenticated_matrix_users(self) -> list[UserID]:
        try:
            members = await self.main_intent.get_room_members(self.mxid)
        except MatrixRequestError:
            return []
        authenticated: list[UserID] = []
        has_bot = self.has_bot
        for member in members:
            if p.Puppet.get_id_from_mxid(member) or member == self.az.bot_mxid:
                continue
            user = await u.User.get_and_start_by_mxid(member)
            authenticated_through_bot = has_bot and user.relaybot_whitelisted
            if authenticated_through_bot or await user.has_full_access(allow_bot=True):
                authenticated.append(user.mxid)
        return authenticated

    async def cleanup_portal(
        self, message: str, puppets_only: bool = False, delete: bool = True
    ) -> None:
        if self.username:
            try:
                await self.main_intent.remove_room_alias(self.alias_localpart)
            except (MatrixRequestError, IntentError):
                self.log.warning("Failed to remove alias when cleaning up room", exc_info=True)
        await self.cleanup_room(self.main_intent, self.mxid, message, puppets_only)
        if delete:
            await self.delete()

    async def delete(self) -> None:
        try:
            del self.by_tgid[self.tgid_full]
        except KeyError:
            pass
        try:
            del self.by_mxid[self.mxid]
        except KeyError:
            pass
        self.name_set = False
        self.avatar_set = False
        self.about = None
        self.next_batch_id = None
        self.first_event_id = None
        self.sponsored_event_id = None
        self.sponsored_event_ts = None
        self.sponsored_msg_random_id = None
        await super().delete()
        await DBMessage.delete_all(self.mxid)
        await DBReaction.delete_all(self.mxid)
        self.deleted = True

    # endregion
    # region Class instance lookup

    async def get_dm_puppet(self) -> p.Puppet | None:
        if not self.is_direct:
            return None
        return await p.Puppet.get_by_tgid(self.tgid)

    async def postinit(self) -> None:
        puppet = await self.get_dm_puppet()
        self._main_intent = puppet.intent_for(self) if self.is_direct else self.az.intent

        if self.tgid:
            self.by_tgid[self.tgid_full] = self
        if self.mxid:
            self.by_mxid[self.mxid] = self

    @classmethod
    async def _yield_portals(
        cls, query: Awaitable[list[DBPortal]]
    ) -> AsyncGenerator[Portal, None]:
        portals = await query
        portal: cls
        for portal in portals:
            try:
                yield cls.by_tgid[portal.tgid_full]
            except KeyError:
                await portal.postinit()
                yield portal

    @classmethod
    def all(cls) -> AsyncGenerator[Portal, None]:
        return cls._yield_portals(super().all())

    @classmethod
    def find_private_chats_of(cls, tg_receiver: TelegramID) -> AsyncGenerator[Portal, None]:
        return cls._yield_portals(super().find_private_chats_of(tg_receiver))

    @classmethod
    def find_private_chats_with(cls, tgid: TelegramID) -> AsyncGenerator[Portal, None]:
        return cls._yield_portals(super().find_private_chats_with(tgid))

    @classmethod
    @async_getter_lock
    async def get_by_mxid(cls, mxid: RoomID, /) -> Portal | None:
        try:
            return cls.by_mxid[mxid]
        except KeyError:
            pass

        portal = cast(cls, await super().get_by_mxid(mxid))
        if portal:
            await portal.postinit()
            return portal

        return None

    @classmethod
    def get_username_from_mx_alias(cls, alias: str) -> str | None:
        return cls.alias_template.parse(alias)

    @classmethod
    async def find_by_username(cls, username: str) -> Portal | None:
        if not username:
            return None

        username = username.lower()

        for _, portal in cls.by_tgid.items():
            if portal.username and portal.username.lower() == username:
                return portal

        portal = cast(cls, await super().find_by_username(username))
        if portal:
            try:
                return cls.by_tgid[portal.tgid_full]
            except KeyError:
                await portal.postinit()
                return portal

        return None

    @classmethod
    @async_getter_lock
    async def get_by_tgid(
        cls, tgid: TelegramID, /, *, tg_receiver: TelegramID | None = None, peer_type: str = None
    ) -> Portal | None:
        if peer_type == "user" and tg_receiver is None:
            raise ValueError('tg_receiver is required when peer_type is "user"')
        tg_receiver = tg_receiver or tgid
        tgid_full = (tgid, tg_receiver)
        try:
            return cls.by_tgid[tgid_full]
        except KeyError:
            pass

        portal = cast(cls, await super().get_by_tgid(tgid, tg_receiver))
        if portal:
            await portal.postinit()
            return portal

        if peer_type:
            cls.log.info(f"Creating portal for {peer_type} {tgid} (receiver {tg_receiver})")
            # TODO enable this for non-release builds
            #      (or add better wrong peer type error handling)
            # if peer_type == "chat":
            #     import traceback
            #     cls.log.info("Chat portal stack trace:\n" + "".join(traceback.format_stack()))
            portal = cls(tgid, peer_type=peer_type, tg_receiver=tg_receiver)
            await portal.postinit()
            await portal.insert()
            return portal

        return None

    @classmethod
    async def get_by_entity(
        cls,
        entity: TypeChat | TypePeer | TypeUser | TypeUserFull | TypeInputPeer,
        tg_receiver: TelegramID | None = None,
        create: bool = True,
    ) -> Portal | None:
        entity_type = type(entity)
        if entity_type in (Chat, ChatFull):
            type_name = "chat"
            entity_id = entity.id
        elif entity_type in (PeerChat, InputPeerChat):
            type_name = "chat"
            entity_id = entity.chat_id
        elif entity_type in (Channel, ChannelFull):
            type_name = "channel"
            entity_id = entity.id
        elif entity_type in (PeerChannel, InputPeerChannel, InputChannel):
            type_name = "channel"
            entity_id = entity.channel_id
        elif entity_type in (User, UserFull):
            type_name = "user"
            entity_id = entity.id
        elif entity_type in (PeerUser, InputPeerUser, InputUser):
            type_name = "user"
            entity_id = entity.user_id
        else:
            raise ValueError(f"Unknown entity type {entity_type.__name__}")
        return await cls.get_by_tgid(
            TelegramID(entity_id),
            tg_receiver=tg_receiver if type_name == "user" else entity_id,
            peer_type=type_name if create else None,
        )

    # endregion
