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
from typing import Awaitable, Dict, List, Optional, Pattern, Tuple, Union, cast, TYPE_CHECKING, Any
from collections import deque
from datetime import datetime
from string import Template
from html import escape as escape_html
import asyncio
import random
import mimetypes
import unicodedata
import hashlib
import logging
import json
import re

import magic
from sqlalchemy import orm
from sqlalchemy.exc import IntegrityError, InvalidRequestError
from sqlalchemy.orm.exc import FlushError

from telethon.tl.functions.messages import (
    AddChatUserRequest, CreateChatRequest, DeleteChatUserRequest, EditChatAdminRequest,
    EditChatPhotoRequest, EditChatTitleRequest, ExportChatInviteRequest, GetFullChatRequest,
    MigrateChatRequest, SetTypingRequest)
from telethon.tl.functions.channels import (
    CreateChannelRequest, EditAboutRequest, EditAdminRequest, EditBannedRequest, EditPhotoRequest,
    EditTitleRequest, ExportInviteRequest, GetParticipantsRequest, InviteToChannelRequest,
    JoinChannelRequest, LeaveChannelRequest, UpdatePinnedMessageRequest, UpdateUsernameRequest)
from telethon.tl.functions.messages import ReadHistoryRequest as ReadMessageHistoryRequest
from telethon.tl.functions.channels import ReadHistoryRequest as ReadChannelHistoryRequest
from telethon.errors import ChatAdminRequiredError, ChatNotModifiedError
from telethon.tl.types import (
    Channel, ChannelAdminRights, ChannelBannedRights, ChannelFull, ChannelParticipantAdmin,
    ChannelParticipantCreator, ChannelParticipantsRecent, ChannelParticipantsSearch, Chat,
    ChatFull, ChatInviteEmpty, ChatParticipantAdmin, ChatParticipantCreator, ChatPhoto,
    DocumentAttributeFilename, DocumentAttributeImageSize, DocumentAttributeSticker,
    DocumentAttributeVideo, FileLocation, GeoPoint, InputChannel, InputChatUploadedPhoto,
    InputPeerChannel, InputPeerChat, InputPeerUser, InputUser, InputUserSelf, Message,
    MessageActionChannelCreate, MessageActionChatAddUser, MessageActionChatCreate,
    MessageActionChatDeletePhoto, MessageActionChatDeleteUser, MessageActionChatEditPhoto,
    MessageActionChatEditTitle, MessageActionChatJoinedByLink, MessageActionChatMigrateTo,
    MessageActionPinMessage, MessageMediaContact, MessageMediaDocument, MessageMediaGeo,
    MessageMediaPhoto, MessageService, PeerChannel, PeerChat, PeerUser, Photo, PhotoCachedSize,
    SendMessageCancelAction, SendMessageTypingAction, TypeChannelParticipant, TypeChat,
    TypeChatParticipant, TypeDocumentAttribute, TypeInputPeer, TypeMessageAction,
    TypeMessageEntity, TypePeer, TypePhotoSize, TypeUpdates, TypeUser, TypeUserFull,
    UpdateChatUserTyping, UpdateNewChannelMessage, UpdateNewMessage, UpdateUserTyping, User,
    UserFull)
from mautrix_appservice import MatrixRequestError, IntentError, AppService, IntentAPI

from .types import MatrixEventID, MatrixRoomID, MatrixUserID, TelegramID
from .context import Context
from .db import Portal as DBPortal, Message as DBMessage, TelegramFile as DBTelegramFile
from . import puppet as p, user as u, formatter, util

if TYPE_CHECKING:
    from .bot import Bot
    from .abstract_user import AbstractUser
    from .config import Config
    from .tgclient import MautrixTelegramClient

mimetypes.init()

config = None  # type: Config

TypeMessage = Union[Message, MessageService]
TypeParticipant = Union[TypeChatParticipant, TypeChannelParticipant]
DedupMXID = Tuple[MatrixEventID, TelegramID]
InviteList = Union[MatrixUserID, List[MatrixUserID]]


class Portal:
    log = logging.getLogger("mau.portal")  # type: logging.Logger
    db = None  # type: orm.Session
    az = None  # type: AppService
    bot = None  # type: Bot
    loop = None  # type: asyncio.AbstractEventLoop

    # Config cache
    filter_mode = None  # type: str
    filter_list = None  # type: List[str]

    public_portals = False  # type: bool
    max_initial_member_sync = -1  # type: int
    sync_channel_members = True  # type: bool

    dedup_pre_db_check = False  # type: bool
    dedup_cache_queue_length = 20  # type: int

    alias_template = None  # type: str
    mx_alias_regex = None  # type: Pattern
    hs_domain = None  # type: str

    # Instance cache
    by_mxid = {}  # type: Dict[MatrixRoomID, Portal]
    by_tgid = {}  # type: Dict[Tuple[TelegramID, TelegramID], Portal]

    def __init__(self, tgid: TelegramID, peer_type: str, tg_receiver: Optional[TelegramID] = None,
                 mxid: Optional[MatrixRoomID] = None, username: Optional[str] = None,
                 megagroup: Optional[bool] = False, title: Optional[str] = None,
                 about: Optional[str] = None, photo_id: Optional[str] = None,
                 config: Optional[str] = None, db_instance: DBPortal = None) -> None:
        self.mxid = mxid  # type: Optional[MatrixRoomID]
        self.tgid = tgid  # type: TelegramID
        self.tg_receiver = tg_receiver or tgid  # type: TelegramID
        self.peer_type = peer_type  # type: str
        self.username = username  # type: str
        self.megagroup = megagroup  # type: bool
        self.title = title  # type: Optional[str]
        self.about = about  # type: str
        self.photo_id = photo_id  # type: str
        self.local_config = json.loads(config or "{}")  # type: Dict[str, Any]
        self._db_instance = db_instance  # type: DBPortal
        self.deleted = False  # type: bool

        self._main_intent = None  # type: IntentAPI
        self._room_create_lock = asyncio.Lock()  # type: asyncio.Lock
        self._temp_pinned_message_id = None  # type: Optional[int]
        self._temp_pinned_message_sender = None  # type: Optional[p.Puppet]

        self._dedup = deque()  # type: deque
        self._dedup_mxid = {}  # type: Dict[str, DedupMXID]
        self._dedup_action = deque()  # type: deque

        self._send_locks = {}  # type: Dict[int, asyncio.Lock]

        if tgid:
            self.by_tgid[self.tgid_full] = self
        if mxid:
            self.by_mxid[mxid] = self

    # region Propegrties

    @property
    def tgid_full(self) -> Tuple[TelegramID, TelegramID]:
        return self.tgid, self.tg_receiver

    @property
    def tgid_log(self) -> str:
        if self.tgid == self.tg_receiver:
            return str(self.tgid)
        return f"{self.tg_receiver}<->{self.tgid}"

    @property
    def peer(self) -> TypePeer:
        if self.peer_type == "user":
            return PeerUser(user_id=self.tgid)
        elif self.peer_type == "chat":
            return PeerChat(chat_id=self.tgid)
        elif self.peer_type == "channel":
            return PeerChannel(channel_id=self.tgid)

    @property
    def has_bot(self) -> bool:
        return bool(self.bot and self.bot.is_in_chat(self.tgid))

    @property
    def main_intent(self) -> IntentAPI:
        if not self._main_intent:
            direct = self.peer_type == "user"
            puppet = p.Puppet.get(self.tgid) if direct else None
            self._main_intent = puppet.intent if direct else self.az.intent
        return self._main_intent

    # endregion
    # region Filtering

    def allow_bridging(self, tgid: Optional[TelegramID] = None) -> bool:
        tgid = tgid or self.tgid
        if self.peer_type == "user":
            return True
        elif self.filter_mode == "whitelist":
            return tgid in self.filter_list
        elif self.filter_mode == "blacklist":
            return tgid not in self.filter_list
        return True

    # endregion
    # region Permission checks

    async def can_user_perform(self, user: 'u.User', event: str, default: int = 50, ref_room_id: str = None) -> bool:
        if user.is_admin:
            return True
        try:
            room_id = self.mxid if self.mxid else ref_room_id
            await self.main_intent.get_power_levels(self.mxid, room_id)
        except MatrixRequestError:
            return False
        return self.main_intent.state_store.has_power_level(
            self.mxid, user.mxid,
            event=f"net.maunium.telegram.{event}",
            default=default)

    # endregion
    # region Deduplication

    @staticmethod
    def _hash_event(event: TypeMessage) -> str:
        # Non-channel messages are unique per-user (wtf telegram), so we have no other choice than
        # to deduplicate based on a hash of the message content.

        # The timestamp is only accurate to the second, so we can't rely solely on that either.
        if isinstance(event, MessageService):
            hash_content = [event.date.timestamp(), event.from_id, event.action]
        else:
            hash_content = [event.date.timestamp(), event.message]
            if event.fwd_from:
                hash_content += [event.fwd_from.from_id, event.fwd_from.channel_id]
            elif isinstance(event, Message) and event.media:
                try:
                    hash_content += {
                        MessageMediaContact: lambda media: [media.user_id],
                        MessageMediaDocument: lambda media: [media.document.id],
                        MessageMediaPhoto: lambda media: [media.photo.id],
                        MessageMediaGeo: lambda media: [media.geo.long, media.geo.lat],
                    }[type(event.media)](event.media)
                except KeyError:
                    pass
        return hashlib.md5("-"
                           .join(str(a) for a in hash_content)
                           .encode("utf-8")
                           ).hexdigest()

    def is_duplicate_action(self, event: TypeMessage) -> bool:
        evt_hash = self._hash_event(event) if self.peer_type != "channel" else event.id
        if evt_hash in self._dedup_action:
            return True

        self._dedup_action.append(evt_hash)

        if len(self._dedup_action) > self.dedup_cache_queue_length:
            self._dedup_action.popleft()
        return False

    def update_duplicate(self, event: TypeMessage, mxid: DedupMXID = None,
                         expected_mxid: Optional[DedupMXID] = None, force_hash: bool = False
                         ) -> Optional[DedupMXID]:
        evt_hash = self._hash_event(
            event) if self.peer_type != "channel" or force_hash else event.id
        try:
            found_mxid = self._dedup_mxid[evt_hash]
        except KeyError:
            return MatrixEventID("None"), TelegramID(0)

        if found_mxid != expected_mxid:
            return found_mxid
        self._dedup_mxid[evt_hash] = mxid
        return None

    def is_duplicate(self, event: TypeMessage, mxid: DedupMXID = None, force_hash: bool = False
                     ) -> Optional[DedupMXID]:
        evt_hash = (self._hash_event(event)
                    if self.peer_type != "channel" or force_hash
                    else event.id)
        if evt_hash in self._dedup:
            return self._dedup_mxid[evt_hash]

        self._dedup_mxid[evt_hash] = mxid
        self._dedup.append(evt_hash)

        if len(self._dedup) > self.dedup_cache_queue_length:
            del self._dedup_mxid[self._dedup.popleft()]
        return None

    def get_input_entity(self, user: 'u.User') -> Awaitable[TypeInputPeer]:
        return user.client.get_input_entity(self.peer)

    # endregion
    # region Matrix room info updating

    async def invite_to_matrix(self, users: InviteList) -> None:
        if isinstance(users, str):
            await self.main_intent.invite(self.mxid, users, check_cache=True)
        elif isinstance(users, list):
            for user in users:
                await self.main_intent.invite(self.mxid, user, check_cache=True)
        else:
            raise ValueError("Invalid invite identifier given to invite_matrix()")

    async def update_matrix_room(self, user: 'AbstractUser', entity: Union[TypeChat, User],
                                 direct: bool, puppet: p.Puppet = None, levels: Dict = None,
                                 users: List[User] = None,
                                 participants: List[TypeParticipant] = None) -> None:
        if not direct:
            await self.update_info(user, entity)
            if not users or not participants:
                users, participants = await self._get_users(user, entity)
            await self.sync_telegram_users(user, users)
            await self.update_telegram_participants(participants, levels)
        else:
            if not puppet:
                puppet = p.Puppet.get(self.tgid)
            await puppet.update_info(user, entity)
            await puppet.intent.join_room(self.mxid)

    async def create_matrix_room(self, user: "AbstractUser", entity: TypeChat = None,
                                 invites: InviteList = None, update_if_exists: bool = True,
                                 synchronous: bool = False) -> Optional[str]:
        if self.mxid:
            if update_if_exists:
                if not entity:
                    entity = await user.client.get_entity(self.peer)
                update = self.update_matrix_room(user, entity, self.peer_type == "user")
                if synchronous:
                    await update
                else:
                    asyncio.ensure_future(update, loop=self.loop)
                await self.invite_to_matrix(invites or [])
            return self.mxid
        async with self._room_create_lock:
            return await self._create_matrix_room(user, entity, invites)

    async def _create_matrix_room(self, user: 'AbstractUser', entity: TypeChat, invites: InviteList
                                  ) -> Optional[MatrixRoomID]:
        direct = self.peer_type == "user"

        if self.mxid:
            return self.mxid

        if not self.allow_bridging():
            return None

        if not entity:
            entity = await user.client.get_entity(self.peer)
            self.log.debug("Fetched data: %s", entity)

        self.log.debug(f"Creating room for {self.tgid_log}")

        try:
            self.title = entity.title
        except AttributeError:
            self.title = None

        puppet = p.Puppet.get(self.tgid) if direct else None
        self._main_intent = puppet.intent if direct else self.az.intent

        if self.peer_type == "channel":
            self.megagroup = entity.megagroup

        if self.peer_type == "channel" and entity.username:
            public = Portal.public_portals
            alias = self._get_alias_localpart(entity.username)
            self.username = entity.username
        else:
            public = False
            # TODO invite link alias?
            alias = None

        if alias:
            # TODO? properly handle existing room aliases
            await self.main_intent.remove_room_alias(alias)

        power_levels = self._get_base_power_levels({}, entity)
        users = participants = None
        if not direct:
            users, participants = await self._get_users(user, entity)
            self._participants_to_power_levels(participants, power_levels)
        initial_state = [{
            "type": "m.room.power_levels",
            "content": power_levels,
        }]

        room_id = await self.main_intent.create_room(alias=alias, is_public=public,
                                                     is_direct=direct, invitees=invites or [],
                                                     name=self.title, initial_state=initial_state)
        if not room_id:
            raise Exception(f"Failed to create room for {self.tgid_log}")

        self.mxid = MatrixRoomID(room_id)
        self.by_mxid[self.mxid] = self
        self.save()
        self.az.state_store.set_power_levels(self.mxid, power_levels)
        user.register_portal(self)
        asyncio.ensure_future(self.update_matrix_room(user, entity, direct, puppet,
                                                      levels=power_levels, users=users,
                                                      participants=participants),
                              loop=self.loop)

        return self.mxid

    def _get_base_power_levels(self, levels: dict = None, entity: TypeChat = None) -> dict:
        levels = levels or {}
        power_level_requirement = (0 if self.peer_type == "chat" and not entity.admins_enabled
                                   else 50)
        levels["ban"] = 99
        levels["invite"] = power_level_requirement if self.peer_type == "chat" else 75
        if "events" not in levels:
            levels["events"] = {}
        levels["events"]["m.room.name"] = power_level_requirement
        levels["events"]["m.room.avatar"] = power_level_requirement
        levels["events"]["m.room.topic"] = 50 if self.peer_type == "channel" else 99
        levels["events"]["m.room.power_levels"] = 75
        levels["events"]["m.room.history_visibility"] = 75
        levels["state_default"] = 50
        levels["users_default"] = 0
        levels["events_default"] = (50 if self.peer_type == "channel" and not entity.megagroup
                                    else 0)
        if "users" not in levels:
            levels["users"] = {
                self.main_intent.mxid: 100
            }
        else:
            levels["users"][self.main_intent.mxid] = 100
        return levels

    @property
    def alias(self) -> Optional[str]:
        if not self.username:
            return None
        return f"#{self._get_alias_localpart()}:{self.hs_domain}"

    def _get_alias_localpart(self, username: Optional[str] = None) -> Optional[str]:
        username = username or self.username
        if not username:
            return None
        return self.alias_template.format(groupname=username)

    def add_bot_chat(self, bot: User) -> None:
        if self.bot and bot.id == self.bot.tgid:
            self.bot.add_chat(self.tgid, self.peer_type)
            return

        user = u.User.get_by_tgid(bot.id)
        if user and user.is_bot:
            user.register_portal(self)

    async def sync_telegram_users(self, source: "AbstractUser", users: List[User]) -> None:
        allowed_tgids = set()
        for entity in users:
            puppet = p.Puppet.get(TelegramID(entity.id))
            if entity.bot:
                self.add_bot_chat(entity)
            allowed_tgids.add(entity.id)
            await puppet.intent.ensure_joined(self.mxid)
            await puppet.update_info(source, entity)

            user = u.User.get_by_tgid(entity.id)
            if user:
                await self.invite_to_matrix(user.mxid)

        # We can't trust the member list if any of the following cases is true:
        #  * There are close to 10 000 users, because Telegram might not be sending all members.
        #  * The member sync count is limited, because then we might ignore some members.
        #  * It's a channel, because non-admins don't have access to the member list.
        trust_member_list = (len(allowed_tgids) < 9900
                             and Portal.max_initial_member_sync == -1
                             and (self.megagroup or self.peer_type != "channel"))
        if trust_member_list:
            joined_mxids = cast(List[MatrixUserID],
                                await self.main_intent.get_room_members(self.mxid))
            for user_mxid in joined_mxids:
                if user_mxid == self.az.bot_mxid:
                    continue
                puppet_id = p.Puppet.get_id_from_mxid(user_mxid)
                if puppet_id and puppet_id not in allowed_tgids:
                    if self.bot and puppet_id == self.bot.tgid:
                        self.bot.remove_chat(self.tgid)
                    await self.main_intent.kick(self.mxid, user_mxid,
                                                "User had left this Telegram chat.")
                    continue
                mx_user = u.User.get_by_mxid(user_mxid, create=False)
                if mx_user and mx_user.is_bot and mx_user.tgid not in allowed_tgids:
                    mx_user.unregister_portal(self)

                if mx_user and not self.has_bot and mx_user.tgid not in allowed_tgids:
                    await self.main_intent.kick(self.mxid, mx_user.mxid,
                                                "You had left this Telegram chat.")
                    continue

    async def add_telegram_user(self, user_id: TelegramID, source: Optional['AbstractUser'] = None
                                ) -> None:
        puppet = p.Puppet.get(user_id)
        if source:
            entity = await source.client.get_entity(PeerUser(user_id))  # type: User
            await puppet.update_info(source, entity)
            await puppet.intent.join_room(self.mxid)

        user = u.User.get_by_tgid(user_id)
        if user:
            user.register_portal(self)
            await self.invite_to_matrix(user.mxid)

    async def delete_telegram_user(self, user_id: TelegramID, sender: p.Puppet) -> None:
        puppet = p.Puppet.get(user_id)
        user = u.User.get_by_tgid(user_id)
        kick_message = (f"Kicked by {sender.displayname}"
                        if sender and sender.tgid != puppet.tgid
                        else "Left Telegram chat")
        if sender and sender.tgid != puppet.tgid:
            await self.main_intent.kick(self.mxid, puppet.mxid, kick_message)
        else:
            await puppet.intent.leave_room(self.mxid)
        if user:
            user.unregister_portal(self)
            await self.main_intent.kick(self.mxid, user.mxid, kick_message)

    async def update_info(self, user: "AbstractUser", entity: TypeChat = None) -> None:
        if self.peer_type == "user":
            self.log.warning(f"Called update_info() for direct chat portal {self.tgid_log}")
            return

        self.log.debug(f"Updating info of {self.tgid_log}")
        if not entity:
            entity = await user.client.get_entity(self.peer)
            self.log.debug("Fetched data: %s", entity)
        changed = False

        if self.peer_type == "channel":
            changed = await self.update_username(entity.username) or changed
            # TODO update about text
            # changed = self.update_about(entity.about) or changed

        changed = await self.update_title(entity.title) or changed

        if isinstance(entity.photo, ChatPhoto):
            changed = await self.update_avatar(user, entity.photo.photo_big) or changed

        if changed:
            self.save()

    async def update_username(self, username: str, save: bool = False) -> bool:
        if self.username != username:
            if self.username:
                await self.main_intent.remove_room_alias(self._get_alias_localpart())
            self.username = username or None
            if self.username:
                await self.main_intent.add_room_alias(self.mxid, self._get_alias_localpart())
                if Portal.public_portals:
                    await self.main_intent.set_join_rule(self.mxid, "public")
            else:
                await self.main_intent.set_join_rule(self.mxid, "invite")

            if save:
                self.save()
            return True
        return False

    async def update_about(self, about: str, save: bool = False) -> bool:
        if self.about != about:
            self.about = about
            await self.main_intent.set_room_topic(self.mxid, self.about)
            if save:
                self.save()
            return True
        return False

    async def update_title(self, title: str, save: bool = False) -> bool:
        if self.title != title:
            self.title = title
            await self.main_intent.set_room_name(self.mxid, self.title)
            if save:
                self.save()
            return True
        return False

    @staticmethod
    def _get_largest_photo_size(photo: Photo) -> TypePhotoSize:
        return max(photo.sizes, key=(lambda photo2: (
            len(photo2.bytes) if isinstance(photo2, PhotoCachedSize) else photo2.size)))

    async def remove_avatar(self, _: "AbstractUser", save: bool = False) -> None:
        await self.main_intent.set_room_avatar(self.mxid, None)
        self.photo_id = None
        if save:
            self.save()

    async def update_avatar(self, user: "AbstractUser", photo: FileLocation,
                            save: bool = False) -> bool:
        photo_id = f"{photo.volume_id}-{photo.local_id}"
        if self.photo_id != photo_id:
            file = await util.transfer_file_to_matrix(self.db, user.client, self.main_intent,
                                                      photo)
            if file:
                await self.main_intent.set_room_avatar(self.mxid, file.mxc)
                self.photo_id = photo_id
                if save:
                    self.save()
                return True
        return False

    async def _get_users(self, user: 'AbstractUser',
                         entity: Union[TypeInputPeer, InputUser, TypeChat, TypeUser]
                         ) -> Tuple[List[TypeUser], List[TypeParticipant]]:
        if self.peer_type == "chat":
            chat = await user.client(GetFullChatRequest(chat_id=self.tgid))
            return chat.users, chat.full_chat.participants.participants
        elif self.peer_type == "channel":
            if not self.megagroup and not Portal.sync_channel_members:
                return [], []

            limit = Portal.max_initial_member_sync
            if limit == 0:
                return [], []

            try:
                if 0 < limit <= 200:
                    response = await user.client(GetParticipantsRequest(
                        entity, ChannelParticipantsRecent(), offset=0, limit=limit, hash=0))
                    return response.users, response.participants
                elif limit > 200 or limit == -1:
                    users, participants = [], []  # type: Tuple[List[TypeUser], List[TypeParticipant]]
                    offset = 0
                    remaining_quota = limit if limit > 0 else 1000000
                    query = (ChannelParticipantsSearch("") if limit == -1
                             else ChannelParticipantsRecent())
                    while True:
                        if remaining_quota <= 0:
                            break
                        response = await user.client(GetParticipantsRequest(
                            entity, query, offset=offset, limit=min(remaining_quota, 100), hash=0))
                        if not response.users:
                            break
                        participants += response.participants
                        users += response.users
                        offset += len(response.participants)
                        remaining_quota -= len(response.participants)
                    return users, participants
            except ChatAdminRequiredError:
                return [], []
        elif self.peer_type == "user":
            return [entity], []
        return [], []

    async def get_invite_link(self, user: 'u.User') -> str:
        if self.peer_type == "user":
            raise ValueError("You can't invite users to private chats.")
        elif self.peer_type == "chat":
            link = await user.client(ExportChatInviteRequest(chat_id=self.tgid))
        elif self.peer_type == "channel":
            if self.username:
                return f"https://t.me/{self.username}"
            link = await user.client(
                ExportInviteRequest(channel=await self.get_input_entity(user)))
        else:
            raise ValueError(f"Invalid peer type '{self.peer_type}' for invite link.")

        if isinstance(link, ChatInviteEmpty):
            raise ValueError("Failed to get invite link.")

        return link.link

    async def get_authenticated_matrix_users(self) -> List['u.User']:
        try:
            members = await self.main_intent.get_room_members(self.mxid)
        except MatrixRequestError:
            return []
        authenticated = []
        has_bot = self.has_bot
        for member_str in members:
            member = MatrixUserID(member_str)
            if p.Puppet.get_id_from_mxid(member) or member == self.main_intent.mxid:
                continue
            user = await u.User.get_by_mxid(member).ensure_started()
            authenticated_through_bot = has_bot and user.relaybot_whitelisted
            if authenticated_through_bot or await user.has_full_access(allow_bot=True):
                authenticated.append(user)
        return authenticated

    @staticmethod
    async def cleanup_room(intent: IntentAPI, room_id: str, message: str = "Portal deleted",
                           puppets_only: bool = False) -> None:
        try:
            members = await intent.get_room_members(room_id)
        except MatrixRequestError:
            members = []
        for user in members:
            puppet = p.Puppet.get_by_mxid(MatrixUserID(user), create=False)
            if user != intent.mxid and (not puppets_only or puppet):
                try:
                    if puppet:
                        await puppet.intent.leave_room(room_id)
                    else:
                        await intent.kick(room_id, user, message)
                except (MatrixRequestError, IntentError):
                    pass
        await intent.leave_room(room_id)

    async def unbridge(self) -> None:
        await self.cleanup_room(self.main_intent, self.mxid, "Room unbridged", puppets_only=True)
        self.delete()

    async def cleanup_and_delete(self) -> None:
        await self.cleanup_room(self.main_intent, self.mxid)
        self.delete()

    # endregion
    # region Matrix event handling

    @staticmethod
    def _get_file_meta(body: str, mime: str) -> str:
        try:
            current_extension = body[body.rindex("."):].lower()
            body = body[:body.rindex(".")]
            if mimetypes.types_map[current_extension] == mime:
                return body + current_extension
        except (ValueError, KeyError):
            pass
        ext_override = {
            "image/jpeg": ".jpg"
        }
        if mime:
            ext = ext_override.get(mime, mimetypes.guess_extension(mime))
            return f"matrix_upload{ext}"
        else:
            return ""

    def get_config(self, key: str) -> Any:
        local = util.recursive_get(self.local_config, key)
        if local is not None:
            return local
        return config[f"bridge.{key}"]

    async def _get_state_change_message(self, event: str, user: 'u.User',
                                        arguments: Optional[Dict] = None) -> Optional[Dict]:
        tpl = self.get_config(f"state_event_formats.{event}")
        if len(tpl) == 0:
            # Empty format means they don't want the message
            return None
        displayname = await self.get_displayname(user)

        tpl_args = dict(mxid=user.mxid,
                        username=user.mxid_localpart,
                        displayname=displayname)
        tpl_args = {**tpl_args, **(arguments or {})}
        message = Template(tpl).safe_substitute(tpl_args)
        return {
            "format": "org.matrix.custom.html",
            "formatted_body": message,
        }

    async def name_change_matrix(self, user: 'u.User', displayname: str, prev_displayname: str,
                                 event_id: MatrixEventID) -> None:
        async with self.require_send_lock(self.bot.tgid):
            message = await self._get_state_change_message(
                "name_change", user,
                dict(displayname=displayname, prev_displayname=prev_displayname))
            if not message:
                return
            response = await self.bot.client.send_message(
                self.peer, message,
                parse_mode=self._matrix_event_to_entities)
            space = self.tgid if self.peer_type == "channel" else self.bot.tgid
            self.is_duplicate(response, (event_id, space))

    async def get_displayname(self, user: 'u.User') -> str:
        return (await self.main_intent.get_displayname(self.mxid, user.mxid)
                or user.mxid)

    def set_typing(self, user: 'u.User', typing: bool = True,
                   action: type = SendMessageTypingAction) -> Awaitable[bool]:
        return user.client(SetTypingRequest(
            self.peer, action() if typing else SendMessageCancelAction()))

    async def mark_read(self, user: 'u.User', event_id: MatrixEventID) -> None:
        if user.is_bot:
            return
        space = self.tgid if self.peer_type == "channel" else user.tgid
        message = DBMessage.query.filter(DBMessage.mxid == event_id,
                                         DBMessage.mx_room == self.mxid,
                                         DBMessage.tg_space == space).one_or_none()
        if not message:
            return
        if self.peer_type == "channel":
            await user.client(ReadChannelHistoryRequest(
                channel=await self.get_input_entity(user), max_id=message.tgid))
        else:
            await user.client(ReadMessageHistoryRequest(peer=self.peer, max_id=message.tgid))

    async def leave_matrix(self, user: 'u.User', source: 'u.User', event_id: MatrixEventID
                           ) -> None:
        if await user.needs_relaybot(self):
            async with self.require_send_lock(self.bot.tgid):
                message = await self._get_state_change_message("leave", user)
                if not message:
                    return
                response = await self.bot.client.send_message(
                    self.peer, message,
                    parse_mode=self._matrix_event_to_entities)
                space = self.tgid if self.peer_type == "channel" else self.bot.tgid
                self.is_duplicate(response, (event_id, space))
            return

        if self.peer_type == "user":
            await self.main_intent.leave_room(self.mxid)
            self.delete()
            try:
                del self.by_tgid[self.tgid_full]
                del self.by_mxid[self.mxid]
            except KeyError:
                pass
        elif source and source.tgid != user.tgid:
            if self.peer_type == "chat":
                await source.client(DeleteChatUserRequest(chat_id=self.tgid, user_id=user.tgid))
            else:
                channel = await self.get_input_entity(source)
                rights = ChannelBannedRights(datetime.fromtimestamp(0), True)
                await source.client(EditBannedRequest(channel=channel,
                                                      user_id=user.tgid,
                                                      banned_rights=rights))
        elif self.peer_type == "chat":
            await user.client(DeleteChatUserRequest(chat_id=self.tgid, user_id=InputUserSelf()))
        elif self.peer_type == "channel":
            channel = await self.get_input_entity(user)
            await user.client(LeaveChannelRequest(channel=channel))

    async def join_matrix(self, user: 'u.User', event_id: MatrixEventID) -> None:
        if await user.needs_relaybot(self):
            async with self.require_send_lock(self.bot.tgid):
                message = await self._get_state_change_message("join", user)
                if not message:
                    return
                response = await self.bot.client.send_message(
                    self.peer, message,
                    parse_mode=self._matrix_event_to_entities)
                space = self.tgid if self.peer_type == "channel" else self.bot.tgid
                self.is_duplicate(response, (event_id, space))
            return

        if self.peer_type == "channel" and not user.is_bot:
            await user.client(JoinChannelRequest(channel=await self.get_input_entity(user)))
        else:
            # We'll just assume the user is already in the chat.
            pass

    async def _apply_msg_format(self, sender: 'u.User', msgtype: str, message: Dict[str, Any]
                                ) -> None:
        if "formatted_body" not in message:
            message["format"] = "org.matrix.custom.html"
            message["formatted_body"] = escape_html(message.get("body", ""))
        body = message["formatted_body"]

        tpl = (self.get_config(f"message_formats.[{msgtype}]")
               or "<b>$sender_displayname</b>: $message")
        displayname = await self.get_displayname(sender)
        tpl_args = dict(sender_mxid=sender.mxid,
                        sender_username=sender.mxid_localpart,
                        sender_displayname=displayname,
                        message=body)
        message["formatted_body"] = Template(tpl).safe_substitute(tpl_args)

    async def _pre_process_matrix_message(self, sender: 'u.User', use_relaybot: bool,
                                          message: Dict[str, Any]) -> None:
        msgtype = message.get("msgtype", "m.text")
        if msgtype == "m.emote":
            await self._apply_msg_format(sender, msgtype, message)
            message["msgtype"] = "m.text"
        elif use_relaybot:
            await self._apply_msg_format(sender, msgtype, message)

    @staticmethod
    def _matrix_event_to_entities(event: Dict[str, Any]) -> Tuple[
        str, Optional[List[TypeMessageEntity]]]:
        try:
            if event.get("format", None) == "org.matrix.custom.html":
                message, entities = formatter.matrix_to_telegram(event.get("formatted_body", ""))
            else:
                message, entities = formatter.matrix_text_to_telegram(event.get("body", ""))
        except KeyError:
            message, entities = None, None
        return message, entities

    def require_send_lock(self, user_id: TelegramID) -> asyncio.Lock:
        if user_id is None:
            raise ValueError("Required send lock for none id")
        try:
            return self._send_locks[user_id]
        except KeyError:
            self._send_locks[user_id] = asyncio.Lock()
            return self._send_locks[user_id]

    def optional_send_lock(self, user_id: TelegramID) -> Optional[asyncio.Lock]:
        if user_id is None:
            return None
        try:
            return self._send_locks[user_id]
        except KeyError:
            return None

    async def _handle_matrix_text(self, sender_id: TelegramID, event_id: MatrixEventID,
                                  space: TelegramID, client: 'MautrixTelegramClient', message: Dict,
                                  reply_to: TelegramID) -> None:
        lock = self.require_send_lock(sender_id)
        async with lock:
            response = await client.send_message(self.peer, message, reply_to=reply_to,
                                                 parse_mode=self._matrix_event_to_entities)
            self._add_telegram_message_to_db(event_id, space, response)

    async def _handle_matrix_file(self, msgtype: str, sender_id: TelegramID,
                                  event_id: MatrixEventID, space: TelegramID,
                                  client: 'MautrixTelegramClient', message: dict,
                                  reply_to: TelegramID) -> None:
        file = await self.main_intent.download_file(message["url"])

        info = message.get("info", {})
        mime = info.get("mimetype", None)

        w, h = None, None

        if msgtype == "m.sticker":
            if mime != "image/gif":
                mime, file, w, h = util.convert_image(file, source_mime=mime, target_type="webp")
            else:
                # Remove sticker description
                message["mxtg_filename"] = "sticker.gif"
                message["body"] = ""
        elif "w" in info and "h" in info:
            w, h = info["w"], info["h"]

        file_name = self._get_file_meta(message["mxtg_filename"], mime)
        attributes = [DocumentAttributeFilename(file_name=file_name)]
        if w and h:
            attributes.append(DocumentAttributeImageSize(w, h))

        caption = message["body"] if message["body"].lower() != file_name.lower() else None

        media = await client.upload_file_direct(file, mime, attributes, file_name)
        lock = self.require_send_lock(sender_id)
        async with lock:
            response = await client.send_media(self.peer, media, reply_to=reply_to,
                                               caption=caption)
            self._add_telegram_message_to_db(event_id, space, response)

    async def _handle_matrix_location(self, sender_id: TelegramID, event_id: MatrixEventID,
                                      space: TelegramID, client: 'MautrixTelegramClient',
                                      message: Dict[str, Any], reply_to: TelegramID) -> None:
        try:
            lat, long = message["geo_uri"][len("geo:"):].split(",")
            lat, long = float(lat), float(long)
        except (KeyError, ValueError):
            self.log.exception("Failed to parse location")
            return None
        caption, entities = self._matrix_event_to_entities(message)
        media = MessageMediaGeo(geo=GeoPoint(lat, long, access_hash=0))

        lock = self.require_send_lock(sender_id)
        async with lock:
            response = await client.send_media(self.peer, media, reply_to=reply_to,
                                               caption=caption, entities=entities)
            self._add_telegram_message_to_db(event_id, space, response)

    def _add_telegram_message_to_db(self, event_id: MatrixEventID, space: TelegramID,
                                    response: TypeMessage) -> None:
        self.log.debug("Handled Matrix message: %s", response)
        self.is_duplicate(response, (event_id, space))
        self.db.add(DBMessage(
            tgid=response.id,
            tg_space=space,
            mx_room=self.mxid,
            mxid=event_id))
        self.db.commit()

    async def handle_matrix_message(self, sender: 'u.User', message: Dict[str, Any],
                                    event_id: MatrixEventID) -> None:
        puppet = p.Puppet.get_by_custom_mxid(sender.mxid)
        if puppet and message.get("net.maunium.telegram.puppet", False):
            self.log.debug("Ignoring puppet-sent message by confirmed puppet user %s", sender.mxid)
            return

        logged_in = not await sender.needs_relaybot(self)
        client = sender.client if logged_in else self.bot.client
        sender_id = sender.tgid if logged_in else self.bot.tgid
        space = (self.tgid if self.peer_type == "channel"  # Channels have their own ID space
                 else (sender.tgid if logged_in else self.bot.tgid))
        reply_to = formatter.matrix_reply_to_telegram(message, space, room_id=self.mxid)

        message["mxtg_filename"] = message["body"]
        await self._pre_process_matrix_message(sender, not logged_in, message)
        msgtype = message["msgtype"]

        if msgtype == "m.notice":
            bridge_notices = self.get_config("bridge_notices.default")
            excepted = sender.mxid in self.get_config("bridge_notices.exceptions")
            if not bridge_notices and not excepted:
                return

        if msgtype == "m.text" or msgtype == "m.notice":
            await self._handle_matrix_text(sender_id, event_id, space, client, message, reply_to)
        elif msgtype == "m.location":
            await self._handle_matrix_location(sender_id, event_id, space, client, message,
                                               reply_to)
        elif msgtype in ("m.sticker", "m.image", "m.file", "m.audio", "m.video"):
            await self._handle_matrix_file(msgtype, sender_id, event_id, space, client, message,
                                           reply_to)
        else:
            self.log.debug(f"Unhandled Matrix event: {message}")

    async def handle_matrix_pin(self, sender: 'u.User',
                                pinned_message: Optional[MatrixEventID]) -> None:
        if self.peer_type != "channel":
            return
        try:
            if not pinned_message:
                await sender.client(UpdatePinnedMessageRequest(channel=self.peer, id=0))
            else:
                message = DBMessage.query.filter(DBMessage.mxid == pinned_message,
                                                 DBMessage.tg_space == self.tgid,
                                                 DBMessage.mx_room == self.mxid).one_or_none()
                await sender.client(UpdatePinnedMessageRequest(channel=self.peer, id=message.tgid))
        except ChatNotModifiedError:
            pass

    async def handle_matrix_deletion(self, deleter: 'u.User', event_id: MatrixEventID) -> None:
        real_deleter = deleter if not await deleter.needs_relaybot(self) else self.bot
        space = self.tgid if self.peer_type == "channel" else real_deleter.tgid
        message = DBMessage.query.filter(DBMessage.mxid == event_id,
                                         DBMessage.tg_space == space,
                                         DBMessage.mx_room == self.mxid).one_or_none()
        if not message:
            return
        await real_deleter.client.delete_messages(self.peer, [message.tgid])

    async def _update_telegram_power_level(self, sender: 'u.User', user_id: TelegramID,
                                           level: int) -> None:
        if self.peer_type == "chat":
            await sender.client(EditChatAdminRequest(
                chat_id=self.tgid, user_id=user_id, is_admin=level >= 50))
        elif self.peer_type == "channel":
            moderator = level >= 50
            admin = level >= 75
            rights = ChannelAdminRights(change_info=moderator, post_messages=moderator,
                                        edit_messages=moderator, delete_messages=moderator,
                                        ban_users=moderator, invite_users=moderator,
                                        invite_link=moderator, pin_messages=moderator,
                                        add_admins=admin)
            await sender.client(
                EditAdminRequest(channel=await self.get_input_entity(sender),
                                 user_id=user_id, admin_rights=rights))

    async def handle_matrix_power_levels(self, sender: 'u.User',
                                         new_users: Dict[MatrixUserID, int],
                                         old_users: Dict[str, int]) -> None:
        # TODO handle all power level changes and bridge exact admin rights to supergroups/channels
        for user, level in new_users.items():
            if not user or user == self.main_intent.mxid or user == sender.mxid:
                continue
            user_id = p.Puppet.get_id_from_mxid(user)
            if not user_id:
                mx_user = u.User.get_by_mxid(user, create=False)
                if not mx_user or not mx_user.tgid:
                    continue
                user_id = mx_user.tgid
            if not user_id or user_id == sender.tgid:
                continue
            if user not in old_users or level != old_users[user]:
                await self._update_telegram_power_level(sender, user_id, level)

    async def handle_matrix_about(self, sender: 'u.User', about: str) -> None:
        if self.peer_type not in {"channel"}:
            return
        channel = await self.get_input_entity(sender)
        await sender.client(EditAboutRequest(channel=channel, about=about))
        self.about = about
        self.save()

    async def handle_matrix_title(self, sender: 'u.User', title: str) -> None:
        if self.peer_type not in {"chat", "channel"}:
            return

        if self.peer_type == "chat":
            response = await sender.client(EditChatTitleRequest(chat_id=self.tgid, title=title))
        else:
            channel = await self.get_input_entity(sender)
            response = await sender.client(EditTitleRequest(channel=channel, title=title))
        self._register_outgoing_actions_for_dedup(response)
        self.title = title
        self.save()

    async def handle_matrix_avatar(self, sender: 'u.User', url: str) -> None:
        if self.peer_type not in {"chat", "channel"}:
            # Invalid peer type
            return

        file = await self.main_intent.download_file(url)
        mime = magic.from_buffer(file, mime=True)
        ext = mimetypes.guess_extension(mime)
        uploaded = await sender.client.upload_file_direct(file, file_name=f"avatar{ext}")
        photo = InputChatUploadedPhoto(file=uploaded)

        if self.peer_type == "chat":
            response = await sender.client(EditChatPhotoRequest(chat_id=self.tgid, photo=photo))
        else:
            channel = await self.get_input_entity(sender)
            response = await sender.client(EditPhotoRequest(channel=channel, photo=photo))
        self._register_outgoing_actions_for_dedup(response)
        for update in response.updates:
            is_photo_update = (isinstance(update, UpdateNewMessage)
                               and isinstance(update.message, MessageService)
                               and isinstance(update.message.action, MessageActionChatEditPhoto))
            if is_photo_update:
                loc = self._get_largest_photo_size(update.message.action.photo).location
                self.photo_id = f"{loc.volume_id}-{loc.local_id}"
                self.save()
                break

    def _register_outgoing_actions_for_dedup(self, response: TypeUpdates) -> None:
        for update in response.updates:
            check_dedup = (isinstance(update, (UpdateNewMessage, UpdateNewChannelMessage))
                           and isinstance(update.message, MessageService))
            if check_dedup:
                self.is_duplicate_action(update.message)

    # endregion
    # region Telegram chat info updating

    async def _get_telegram_users_in_matrix_room(self) -> List[TelegramID]:
        user_tgids = set()
        user_mxids = await self.main_intent.get_room_members(self.mxid, ("join", "invite"))
        for user_str in user_mxids:
            user = MatrixUserID(user_str)
            if user == self.az.bot_mxid:
                continue
            mx_user = u.User.get_by_mxid(user, create=False)
            if mx_user and mx_user.tgid:
                user_tgids.add(mx_user.tgid)
            puppet_id = p.Puppet.get_id_from_mxid(user)
            if puppet_id:
                user_tgids.add(puppet_id)
        return list(user_tgids)

    async def upgrade_telegram_chat(self, source: 'u.User') -> None:
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
        self.peer_type = "channel"
        self.migrate_and_save(TelegramID(entity.id))
        await self.update_info(source, entity)

    async def set_telegram_username(self, source: 'u.User', username: str) -> None:
        if self.peer_type != "channel":
            raise ValueError("Only channels and supergroups have usernames.")
        await source.client(
            UpdateUsernameRequest(await self.get_input_entity(source), username))
        if await self.update_username(username):
            self.save()

    async def create_telegram_chat(self, source: 'u.User', supergroup: bool = False) -> None:
        if not self.mxid:
            raise ValueError("Can't create Telegram chat for portal without Matrix room.")
        elif self.tgid:
            raise ValueError("Can't create Telegram chat for portal with existing Telegram chat.")

        invites = await self._get_telegram_users_in_matrix_room()
        if len(invites) < 2:
            raise ValueError("Not enough Telegram users to create a chat")

        if self.peer_type == "chat":
            response = await source.client(CreateChatRequest(title=self.title, users=invites))
            entity = response.chats[0]
        elif self.peer_type == "channel":
            response = await source.client(CreateChannelRequest(title=self.title,
                                                                about=self.about or "",
                                                                megagroup=supergroup))
            entity = response.chats[0]
            await source.client(InviteToChannelRequest(
                channel=await source.client.get_input_entity(entity),
                users=invites))
        else:
            raise ValueError("Invalid peer type for Telegram chat creation")

        self.tgid = entity.id
        self.tg_receiver = self.tgid
        self.by_tgid[self.tgid_full] = self
        await self.update_info(source, entity)
        self.db.add(self.db_instance)
        self.save()

        if self.bot and self.bot.tgid in invites:
            self.bot.add_chat(self.tgid, self.peer_type)

        levels = await self.main_intent.get_power_levels(self.mxid)
        bot_level = self._get_bot_level(levels)
        if bot_level == 100:
            levels = self._get_base_power_levels(levels, entity)
            await self.main_intent.set_power_levels(self.mxid, levels)
        await self.handle_matrix_power_levels(source, levels["users"], {})

    async def invite_telegram(self, source: 'u.User',
                              puppet: Union[p.Puppet, "AbstractUser"]) -> None:
        if self.peer_type == "chat":
            await source.client(
                AddChatUserRequest(chat_id=self.tgid, user_id=puppet.tgid, fwd_limit=0))
        elif self.peer_type == "channel":
            await source.client(InviteToChannelRequest(channel=self.peer, users=[puppet.tgid]))
        else:
            raise ValueError("Invalid peer type for Telegram user invite")

    # endregion
    # region Telegram event handling

    async def handle_telegram_typing(self, user: p.Puppet,
                                     _: Union[UpdateUserTyping, UpdateChatUserTyping]) -> None:
        await user.intent.set_typing(self.mxid, is_typing=True)

    def get_external_url(self, evt: Message) -> Optional[str]:
        if self.peer_type == "channel" and self.username is not None:
            return f"https://t.me/{self.username}/{evt.id}"
        return None

    async def handle_telegram_photo(self, source: "AbstractUser", intent: IntentAPI, evt: Message,
                                    relates_to: Dict = None) -> Optional[Dict]:
        largest_size = self._get_largest_photo_size(evt.media.photo)
        file = await util.transfer_file_to_matrix(self.db, source.client, intent,
                                                  largest_size.location)
        if not file:
            return None
        if self.get_config("inline_images") and (evt.message
                                                 or evt.fwd_from or evt.reply_to_msg_id):
            text, html, relates_to = await formatter.telegram_to_matrix(
                evt, source, self.main_intent,
                prefix_html=f"<img src='{file.mxc}' alt='Inline Telegram photo'/><br/>",
                prefix_text="Inline image: ")
            await intent.set_typing(self.mxid, is_typing=False)
            return await intent.send_text(self.mxid, text, html=html, relates_to=relates_to,
                                          timestamp=evt.date,
                                          external_url=self.get_external_url(evt))
        info = {
            "h": largest_size.h,
            "w": largest_size.w,
            "size": len(largest_size.bytes) if (
                isinstance(largest_size, PhotoCachedSize)) else largest_size.size,
            "orientation": 0,
            "mimetype": file.mime_type,
        }
        ext_override = {
            "image/jpeg": ".jpg"
        }
        name = "image" + ext_override.get(file.mime_type, mimetypes.guess_extension(file.mime_type))
        await intent.set_typing(self.mxid, is_typing=False)
        result = await intent.send_image(self.mxid, file.mxc, info=info, text=name,
                                         relates_to=relates_to, timestamp=evt.date,
                                         external_url=self.get_external_url(evt))
        if evt.message:
            text, html, _ = await formatter.telegram_to_matrix(evt, source, self.main_intent)
            await intent.send_text(self.mxid, text, html=html, timestamp=evt.date,
                                   external_url=self.get_external_url(evt))
        return result

    @staticmethod
    def _parse_telegram_document_attributes(attributes: List[TypeDocumentAttribute]) -> Dict:
        attrs = {
            "name": None,
            "mime_type": None,
            "is_sticker": False,
            "sticker_alt": None,
            "width": None,
            "height": None,
        }  # type: Dict
        for attr in attributes:
            if isinstance(attr, DocumentAttributeFilename):
                attrs["name"] = attrs["name"] or attr.file_name
                attrs["mime_type"], _ = mimetypes.guess_type(attr.file_name)
            elif isinstance(attr, DocumentAttributeSticker):
                attrs["is_sticker"] = True
                attrs["sticker_alt"] = attr.alt
            elif isinstance(attr, DocumentAttributeVideo):
                attrs["width"], attrs["height"] = attr.w, attr.h
        return attrs

    @staticmethod
    def _parse_telegram_document_meta(evt: Message, file: DBTelegramFile, attrs: Dict
                                      ) -> Tuple[Dict, str]:
        document = evt.media.document
        name = evt.message or attrs["name"]
        if attrs["is_sticker"]:
            alt = attrs["sticker_alt"]
            if len(alt) > 0:
                name = f"{alt} ({unicodedata.name(alt[0]).lower()})"

        mime_type = document.mime_type or file.mime_type
        info = {
            "size": file.size,
            "mimetype": mime_type,
        }

        if attrs["mime_type"] and not file.was_converted:
            file.mime_type = attrs["mime_type"] or file.mime_type
        if file.width and file.height:
            info["w"], info["h"] = file.width, file.height
        elif attrs["width"] and attrs["height"]:
            info["w"], info["h"] = attrs["width"], attrs["height"]

        if file.thumbnail:
            info["thumbnail_url"] = file.thumbnail.mxc
            info["thumbnail_info"] = {
                "mimetype": file.thumbnail.mime_type,
                "h": file.thumbnail.height or document.thumb.h,
                "w": file.thumbnail.width or document.thumb.w,
                "size": file.thumbnail.size,
            }

        return info, name

    async def handle_telegram_document(self, source: "AbstractUser", intent: IntentAPI,
                                       evt: Message,
                                       relates_to: dict = None) -> Optional[Dict]:
        document = evt.media.document
        attrs = self._parse_telegram_document_attributes(document.attributes)

        file = await util.transfer_file_to_matrix(self.db, source.client, intent, document,
                                                  document.thumb, is_sticker=attrs["is_sticker"])
        if not file:
            return None

        info, name = self._parse_telegram_document_meta(evt, file, attrs)

        await intent.set_typing(self.mxid, is_typing=False)

        kwargs = {
            "room_id": self.mxid,
            "url": file.mxc,
            "info": info,
            "text": name,
            "relates_to": relates_to,
            "timestamp": evt.date,
            "external_url": self.get_external_url(evt)
        }

        if attrs["is_sticker"] and self.get_config("native_stickers"):
            return await intent.send_sticker(**kwargs)

        mime_type = info["mimetype"]
        if mime_type.startswith("video/"):
            kwargs["file_type"] = "m.video"
        elif mime_type.startswith("audio/"):
            kwargs["file_type"] = "m.audio"
        elif mime_type.startswith("image/"):
            kwargs["file_type"] = "m.image"
        else:
            kwargs["file_type"] = "m.file"
        return await intent.send_file(**kwargs)

    def handle_telegram_location(self, _: "AbstractUser", intent: IntentAPI, evt: Message,
                                 relates_to: dict = None) -> Awaitable[dict]:
        location = evt.media.geo
        long = location.long
        lat = location.lat
        long_char = "E" if long > 0 else "W"
        lat_char = "N" if lat > 0 else "S"
        rounded_long = abs(round(long * 100000) / 100000)
        rounded_lat = abs(round(lat * 100000) / 100000)

        body = f"{rounded_lat}° {lat_char}, {rounded_long}° {long_char}"

        url = f"https://maps.google.com/?q={lat},{long}"

        formatted_body = f"Location: <a href='{url}'>{body}</a>"
        # At least riot-web ignores formatting in m.location messages,
        # so we'll add a plaintext link.
        body = f"Location: {body}\n{url}"

        return intent.send_message(self.mxid, {
            "msgtype": "m.location",
            "geo_uri": f"geo:{lat},{long}",
            "body": body,
            "format": "org.matrix.custom.html",
            "formatted_body": formatted_body,
            "m.relates_to": relates_to or None,
        }, timestamp=evt.date, external_url=self.get_external_url(evt))

    async def handle_telegram_text(self, source: "AbstractUser", intent: IntentAPI, is_bot: bool,
                                   evt: Message) -> dict:
        self.log.debug(f"Sending {evt.message} to {self.mxid} by {intent.mxid}")
        text, html, relates_to = await formatter.telegram_to_matrix(evt, source, self.main_intent)
        await intent.set_typing(self.mxid, is_typing=False)
        msgtype = "m.notice" if is_bot and self.get_config("bot_messages_as_notices") else "m.text"
        return await intent.send_text(self.mxid, text, html=html, relates_to=relates_to,
                                      msgtype=msgtype, timestamp=evt.date,
                                      external_url=self.get_external_url(evt))

    async def handle_telegram_edit(self, source: "AbstractUser", sender: p.Puppet,
                                   evt: Message) -> None:
        if not self.mxid:
            return
        elif not self.get_config("edits_as_replies"):
            self.log.debug("Edits as replies disabled, ignoring edit event...")
            return

        lock = self.optional_send_lock(sender.tgid if sender else None)
        if lock:
            async with lock:
                pass

        tg_space = self.tgid if self.peer_type == "channel" else source.tgid

        temporary_identifier = MatrixEventID(
            f"${random.randint(1000000000000,9999999999999)}TGBRIDGEDITEMP")
        duplicate_found = self.is_duplicate(evt, (temporary_identifier, tg_space), force_hash=True)
        if duplicate_found:
            mxid, other_tg_space = duplicate_found
            if tg_space != other_tg_space:
                msg = DBMessage.query.get((evt.id, tg_space))
                msg.mxid = mxid
                msg.mx_room = self.mxid
                self.db.commit()
            return

        evt.reply_to_msg_id = evt.id
        text, html, relates_to = await formatter.telegram_to_matrix(evt, source, self.main_intent,
                                                                    is_edit=True)
        intent = sender.intent if sender else self.main_intent
        await intent.set_typing(self.mxid, is_typing=False)
        response = await intent.send_text(self.mxid, text, html=html, relates_to=relates_to,
                                          external_url=self.get_external_url(evt))

        mxid = response["event_id"]

        msg = DBMessage.query.get((evt.id, tg_space))
        if not msg:
            self.log.info(f"Didn't find edited message {evt.id}@{tg_space} (src {source.tgid}) "
                          "in database.")
            # Oh crap
            return
        msg.mxid = mxid
        msg.mx_room = self.mxid
        DBMessage.query \
            .filter(DBMessage.mx_room == self.mxid,
                    DBMessage.mxid == temporary_identifier) \
            .update({"mxid": mxid})
        self.db.commit()

    async def handle_telegram_message(self, source: "AbstractUser", sender: p.Puppet,
                                      evt: Message) -> None:
        if not self.mxid:
            await self.create_matrix_room(source, invites=[source.mxid], update_if_exists=False)

        lock = self.optional_send_lock(sender.tgid if sender else None)
        if lock:
            async with lock:
                pass

        tg_space = self.tgid if self.peer_type == "channel" else source.tgid

        temporary_identifier = MatrixEventID(
            f"${random.randint(1000000000000,9999999999999)}TGBRIDGETEMP")
        duplicate_found = self.is_duplicate(evt, (temporary_identifier, tg_space))
        if duplicate_found:
            mxid, other_tg_space = duplicate_found
            self.log.debug(f"Ignoring message {evt.id}@{tg_space} (src {source.tgid}) "
                           f"as it was already handled (in space {other_tg_space})")
            if tg_space != other_tg_space:
                self.db.add(
                    DBMessage(tgid=evt.id, mx_room=self.mxid, mxid=mxid, tg_space=tg_space))
                self.db.commit()
            return

        if self.dedup_pre_db_check and self.peer_type == "channel":
            msg = DBMessage.query.get((evt.id, tg_space))
            if msg:
                self.log.debug(f"Ignoring message {evt.id} (src {source.tgid}) as it was already"
                               f"handled into {msg.mxid}. This duplicate was catched in the db "
                               "check. If you get this message often, consider increasing"
                               "bridge.deduplication.cache_queue_length in the config.")
                return

        if sender and not sender.displayname:
            self.log.debug(f"Telegram user {sender.tgid} sent a message, but doesn't have a"
                           "displayname, updating info...")
            entity = await source.client.get_entity(PeerUser(sender.tgid))
            await sender.update_info(source, entity)

        allowed_media = (MessageMediaPhoto, MessageMediaDocument, MessageMediaGeo)
        media = evt.media if hasattr(evt, "media") and isinstance(evt.media,
                                                                  allowed_media) else None
        intent = sender.intent if sender else self.main_intent
        if not media and evt.message:
            is_bot = sender.is_bot if sender else False
            response = await self.handle_telegram_text(source, intent, is_bot, evt)
        elif media:
            relates_to = formatter.telegram_reply_to_matrix(evt, source)
            if isinstance(media, MessageMediaPhoto):
                response = await self.handle_telegram_photo(source, intent, evt, relates_to)
            elif isinstance(media, MessageMediaDocument):
                response = await self.handle_telegram_document(source, intent, evt, relates_to)
            elif isinstance(media, MessageMediaGeo):
                response = await self.handle_telegram_location(source, intent, evt, relates_to)
            else:
                self.log.debug("Unhandled Telegram media: %s", media)
                return
        else:
            self.log.debug("Unhandled Telegram message: %s", evt)
            return

        if not response:
            return

        mxid = response["event_id"]

        prev_id = self.update_duplicate(evt, (mxid, tg_space), (temporary_identifier, tg_space))
        if prev_id:
            self.log.debug(f"Sent message {evt.id}@{tg_space} to Matrix as {mxid}. "
                           f"Temporary dedup identifier was {temporary_identifier}, "
                           f"but dedup map contained {prev_id[1]} instead! -- "
                           "This was probably a race condition caused by Telegram sending updates"
                           "to other clients before responding to the sender. I'll just redact "
                           "the likely duplicate message now.")
            await intent.redact(self.mxid, mxid)
            return

        self.log.debug("Handled Telegram message: %s", evt)
        try:
            self.db.add(DBMessage(tgid=evt.id, mx_room=self.mxid, mxid=mxid, tg_space=tg_space))
            self.db.commit()
            DBMessage.query \
                .filter(DBMessage.mx_room == self.mxid,
                        DBMessage.mxid == temporary_identifier) \
                .update({"mxid": mxid})
        except FlushError as e:
            self.log.exception(f"{e.__class__.__name__} while saving message mapping. "
                               "This might mean that an update was handled after it left the "
                               "dedup cache queue. You can try enabling bridge.deduplication."
                               "pre_db_check in the config.")
            await intent.redact(self.mxid, mxid)
        except (IntegrityError, InvalidRequestError) as e:
            self.log.exception(f"{e.__class__.__name__} while saving message mapping. "
                               "This might mean that an update was handled after it left the "
                               "dedup cache queue. You can try enabling bridge.deduplication."
                               "pre_db_check in the config.")
            self.db.rollback()
            await intent.redact(self.mxid, mxid)

    async def _create_room_on_action(self, source: "AbstractUser",
                                     action: TypeMessageAction) -> bool:
        if source.is_relaybot:
            return False
        create_and_exit = (MessageActionChatCreate, MessageActionChannelCreate)
        create_and_continue = (MessageActionChatAddUser, MessageActionChatJoinedByLink)
        if isinstance(action, create_and_exit) or isinstance(action, create_and_continue):
            await self.create_matrix_room(source, invites=[source.mxid],
                                          update_if_exists=isinstance(action, create_and_exit))
        if not isinstance(action, create_and_continue):
            return False
        return True

    async def handle_telegram_action(self, source: "AbstractUser", sender: p.Puppet,
                                     update: MessageService) -> None:
        action = update.action
        should_ignore = ((not self.mxid and not await self._create_room_on_action(source, action))
                         or self.is_duplicate_action(update))
        if should_ignore or not self.mxid:
            return
        # TODO figure out how to see changes to about text / channel username
        if isinstance(action, MessageActionChatEditTitle):
            await self.update_title(action.title, save=True)
        elif isinstance(action, MessageActionChatEditPhoto):
            largest_size = self._get_largest_photo_size(action.photo)
            await self.update_avatar(source, largest_size.location, save=True)
        elif isinstance(action, MessageActionChatDeletePhoto):
            await self.remove_avatar(source, save=True)
        elif isinstance(action, MessageActionChatAddUser):
            for user_id in action.users:
                await self.add_telegram_user(TelegramID(user_id), source)
        elif isinstance(action, MessageActionChatJoinedByLink):
            await self.add_telegram_user(sender.id, source)
        elif isinstance(action, MessageActionChatDeleteUser):
            await self.delete_telegram_user(TelegramID(action.user_id), sender)
        elif isinstance(action, MessageActionChatMigrateTo):
            self.peer_type = "channel"
            self.migrate_and_save(TelegramID(action.channel_id))
            await sender.intent.send_emote(self.mxid, "upgraded this group to a supergroup.")
        elif isinstance(action, MessageActionPinMessage):
            await self.receive_telegram_pin_sender(sender)
        else:
            self.log.debug("Unhandled Telegram action in %s: %s", self.title, action)

    async def set_telegram_admin(self, user_id: TelegramID) -> None:
        puppet = p.Puppet.get(user_id)
        user = u.User.get_by_tgid(user_id)

        levels = await self.main_intent.get_power_levels(self.mxid)
        if user:
            levels["users"][user.mxid] = 50
        if puppet:
            levels["users"][puppet.mxid] = 50
        await self.main_intent.set_power_levels(self.mxid, levels)

    async def receive_telegram_pin_sender(self, sender: p.Puppet) -> None:
        self._temp_pinned_message_sender = sender
        if self._temp_pinned_message_id:
            await self.update_telegram_pin()

    async def update_telegram_pin(self) -> None:
        intent = (self._temp_pinned_message_sender.intent
                  if self._temp_pinned_message_sender else self.main_intent)
        msg_id = self._temp_pinned_message_id
        self._temp_pinned_message_id = None
        self._temp_pinned_message_sender = None

        message = DBMessage.query.get((msg_id, self.tgid))
        if message:
            await intent.set_pinned_messages(self.mxid, [message.mxid])
        else:
            await intent.set_pinned_messages(self.mxid, [])

    async def receive_telegram_pin_id(self, msg_id: int) -> None:
        if msg_id == 0:
            return await self.update_telegram_pin()
        self._temp_pinned_message_id = msg_id
        if self._temp_pinned_message_sender:
            await self.update_telegram_pin()

    @staticmethod
    def _get_level_from_participant(participant: TypeParticipant, _: Dict) -> int:
        # TODO use the power level requirements to get better precision in channels
        if isinstance(participant, (ChatParticipantAdmin, ChannelParticipantAdmin)):
            return 50
        elif isinstance(participant, (ChatParticipantCreator, ChannelParticipantCreator)):
            return 95
        return 0

    @staticmethod
    def _participant_to_power_levels(levels: dict, user: Union['u.User', p.Puppet], new_level: int,
                                     bot_level: int) -> bool:
        new_level = min(new_level, bot_level)
        default_level = levels["users_default"] if "users_default" in levels else 0
        try:
            user_level = int(levels["users"][user.mxid])
        except (ValueError, KeyError):
            user_level = default_level
        if user_level != new_level and user_level < bot_level:
            levels["users"][user.mxid] = new_level
            return True
        return False

    def _get_bot_level(self, levels: dict) -> int:
        try:
            return levels["users"][self.main_intent.mxid]
        except KeyError:
            try:
                return levels["users_default"]
            except KeyError:
                return 0

    @staticmethod
    def _get_powerlevel_level(levels: dict) -> int:
        try:
            return levels["events"]["m.room.power_levels"]
        except KeyError:
            try:
                return levels["state_default"]
            except KeyError:
                return 50

    def _participants_to_power_levels(self, participants: List[TypeParticipant], levels: Dict
                                      ) -> bool:
        bot_level = self._get_bot_level(levels)
        if bot_level < self._get_powerlevel_level(levels):
            return False
        changed = False
        admin_power_level = min(75 if self.peer_type == "channel" else 50, bot_level)
        if levels["events"]["m.room.power_levels"] != admin_power_level:
            changed = True
            levels["events"]["m.room.power_levels"] = admin_power_level

        for participant in participants:
            puppet = p.Puppet.get(TelegramID(participant.user_id))
            user = u.User.get_by_tgid(participant.user_id)
            new_level = self._get_level_from_participant(participant, levels)

            if user:
                user.register_portal(self)
                changed = self._participant_to_power_levels(levels, user, new_level,
                                                            bot_level) or changed

            if puppet:
                changed = self._participant_to_power_levels(levels, puppet, new_level,
                                                            bot_level) or changed
        return changed

    async def update_telegram_participants(self, participants: List[TypeParticipant],
                                           levels: dict = None) -> None:
        if not levels:
            levels = await self.main_intent.get_power_levels(self.mxid)
        if self._participants_to_power_levels(participants, levels):
            await self.main_intent.set_power_levels(self.mxid, levels)

    async def set_telegram_admins_enabled(self, enabled: bool) -> None:
        level = 50 if enabled else 10
        levels = await self.main_intent.get_power_levels(self.mxid)
        levels["invite"] = level
        levels["events"]["m.room.name"] = level
        levels["events"]["m.room.avatar"] = level
        await self.main_intent.set_power_levels(self.mxid, levels)

    # endregion
    # region Database conversion

    @property
    def db_instance(self) -> DBPortal:
        if not self._db_instance:
            self._db_instance = self.new_db_instance()
        return self._db_instance

    def new_db_instance(self) -> DBPortal:
        return DBPortal(tgid=self.tgid, tg_receiver=self.tg_receiver, peer_type=self.peer_type,
                        mxid=self.mxid, username=self.username, megagroup=self.megagroup,
                        title=self.title, about=self.about, photo_id=self.photo_id,
                        config=json.dumps(self.local_config))

    def migrate_and_save(self, new_id: TelegramID) -> None:
        existing = DBPortal.query.get(self.tgid_full)
        if existing:
            self.db.delete(existing)
        try:
            del self.by_tgid[self.tgid_full]
        except KeyError:
            pass
        self.tgid = new_id
        self.tg_receiver = new_id
        self.by_tgid[self.tgid_full] = self
        self.save()

    def save(self) -> None:
        self.db_instance.mxid = self.mxid
        self.db_instance.username = self.username
        self.db_instance.title = self.title
        self.db_instance.about = self.about
        self.db_instance.photo_id = self.photo_id
        self.db_instance.config = json.dumps(self.local_config)
        self.db.commit()

    def delete(self) -> None:
        try:
            del self.by_tgid[self.tgid_full]
        except KeyError:
            pass
        try:
            del self.by_mxid[self.mxid]
        except KeyError:
            pass
        if self._db_instance:
            self.db.delete(self._db_instance)
            self.db.commit()
        self.deleted = True

    @classmethod
    def from_db(cls, db_portal: DBPortal) -> 'Portal':
        return Portal(tgid=db_portal.tgid, tg_receiver=db_portal.tg_receiver,
                      peer_type=db_portal.peer_type, mxid=db_portal.mxid,
                      username=db_portal.username, megagroup=db_portal.megagroup,
                      title=db_portal.title, about=db_portal.about, photo_id=db_portal.photo_id,
                      config=db_portal.config, db_instance=db_portal)

    # endregion
    # region Class instance lookup

    @classmethod
    def get_by_mxid(cls, mxid: MatrixRoomID) -> Optional['Portal']:
        try:
            return cls.by_mxid[mxid]
        except KeyError:
            pass

        portal = DBPortal.query.filter(DBPortal.mxid == mxid).one_or_none()
        if portal:
            return cls.from_db(portal)

        return None

    @classmethod
    def get_username_from_mx_alias(cls, alias: str) -> Optional[str]:
        match = cls.mx_alias_regex.match(alias)
        if match:
            return match.group(1)
        return None

    @classmethod
    def find_by_username(cls, username: str) -> Optional['Portal']:
        if not username:
            return None

        for _, portal in cls.by_tgid.items():
            if portal.username and portal.username.lower() == username.lower():
                return portal

        dbportal = DBPortal.query.filter(DBPortal.username == username).one_or_none()
        if dbportal:
            return cls.from_db(dbportal)

        return None

    @classmethod
    def get_by_tgid(cls, tgid: TelegramID, tg_receiver: Optional[TelegramID] = None,
                    peer_type: str = None) -> Optional['Portal']:
        tg_receiver = tg_receiver or tgid
        tgid_full = (tgid, tg_receiver)
        try:
            return cls.by_tgid[tgid_full]
        except KeyError:
            pass

        portal = DBPortal.query.get(tgid_full)
        if portal:
            return cls.from_db(portal)

        if peer_type:
            portal = Portal(tgid, peer_type=peer_type, tg_receiver=tg_receiver)
            cls.db.add(portal.db_instance)
            cls.db.commit()
            return portal

        return None

    @classmethod
    def get_by_entity(cls, entity: Union[TypeChat, TypePeer, TypeUser, TypeUserFull,
                                         TypeInputPeer],
                      receiver_id: Optional[TelegramID] = None, create: bool = True
                      ) -> Optional['Portal']:
        entity_type = type(entity)
        if entity_type in {Chat, ChatFull}:
            type_name = "chat"
            entity_id = entity.id
        elif entity_type in {PeerChat, InputPeerChat}:
            type_name = "chat"
            entity_id = entity.chat_id
        elif entity_type in {Channel, ChannelFull}:
            type_name = "channel"
            entity_id = entity.id
        elif entity_type in {PeerChannel, InputPeerChannel, InputChannel}:
            type_name = "channel"
            entity_id = entity.channel_id
        elif entity_type in {User, UserFull}:
            type_name = "user"
            entity_id = entity.id
        elif entity_type in {PeerUser, InputPeerUser, InputUser}:
            type_name = "user"
            entity_id = entity.user_id
        else:
            raise ValueError(f"Unknown entity type {entity_type.__name__}")
        return cls.get_by_tgid(TelegramID(entity_id),
                               receiver_id if type_name == "user" else entity_id,
                               type_name if create else None)

    # endregion


def init(context: Context) -> None:
    global config
    Portal.az, Portal.db, config, Portal.loop, Portal.bot = context.core
    Portal.max_initial_member_sync = config["bridge.max_initial_member_sync"]
    Portal.sync_channel_members = config["bridge.sync_channel_members"]
    Portal.public_portals = config["bridge.public_portals"]
    Portal.filter_mode = config["bridge.filter.mode"]
    Portal.filter_list = config["bridge.filter.list"]
    Portal.dedup_pre_db_check = config["bridge.deduplication.pre_db_check"]
    Portal.dedup_cache_queue_length = config["bridge.deduplication.cache_queue_length"]
    Portal.alias_template = config.get("bridge.alias_template", "telegram_{groupname}")
    Portal.hs_domain = config["homeserver.domain"]
    Portal.mx_alias_regex = re.compile(
        f"#{Portal.alias_template.format(groupname='(.+)')}:{Portal.hs_domain}")
