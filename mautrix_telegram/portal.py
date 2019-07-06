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
from typing import Awaitable, Dict, List, Optional, Pattern, Tuple, Union, cast, TYPE_CHECKING, Any
from collections import deque
from datetime import datetime
from string import Template
from html import escape as escape_html
import asyncio
import random
import mimetypes
import codecs
import unicodedata
import base64
import hashlib
import logging
import json
import re

import magic
from sqlalchemy.exc import IntegrityError

from telethon.tl.functions.messages import (
    AddChatUserRequest, CreateChatRequest, DeleteChatUserRequest, EditChatAdminRequest,
    EditChatPhotoRequest, EditChatTitleRequest, ExportChatInviteRequest, GetFullChatRequest,
    UpdatePinnedMessageRequest, MigrateChatRequest, SetTypingRequest, EditChatAboutRequest)
from telethon.tl.functions.channels import (
    CreateChannelRequest, EditAdminRequest, EditBannedRequest, EditPhotoRequest, EditTitleRequest,
    GetParticipantsRequest, InviteToChannelRequest, JoinChannelRequest, LeaveChannelRequest,
    UpdateUsernameRequest)
from telethon.tl.functions.messages import ReadHistoryRequest as ReadMessageHistoryRequest
from telethon.tl.functions.channels import ReadHistoryRequest as ReadChannelHistoryRequest
from telethon.errors import (ChatAdminRequiredError, ChatNotModifiedError, PhotoExtInvalidError,
                             PhotoInvalidDimensionsError, PhotoSaveFileInvalidError)
from telethon.tl.patched import Message, MessageService
from telethon.tl.types import (
    Channel, ChatAdminRights, ChatBannedRights, ChannelFull, ChannelParticipantAdmin, Document,
    ChannelParticipantCreator, ChannelParticipantsRecent, ChannelParticipantsSearch, Chat,
    ChatFull, ChatInviteEmpty, ChatParticipantAdmin, ChatParticipantCreator, ChatPhoto, Poll,
    DocumentAttributeFilename, DocumentAttributeImageSize, DocumentAttributeSticker, PhotoEmpty,
    DocumentAttributeVideo, GeoPoint, InputChannel, InputChatUploadedPhoto, InputPhotoFileLocation,
    InputPeerChannel, InputPeerChat, InputPeerUser, InputUser, InputUserSelf, MessageMediaPoll,
    MessageActionChannelCreate, MessageActionChatAddUser, MessageActionChatCreate, ChatPhotoEmpty,
    MessageActionChatDeletePhoto, MessageActionChatDeleteUser, MessageActionChatEditPhoto,
    MessageActionChatEditTitle, MessageActionChatJoinedByLink, MessageActionChatMigrateTo,
    MessageActionPinMessage, MessageActionGameScore, MessageMediaContact, MessageMediaDocument,
    MessageMediaGeo, MessageMediaPhoto, MessageMediaUnsupported, MessageMediaGame,
    PeerChannel, PeerChat, PeerUser, Photo, PhotoCachedSize, SendMessageCancelAction,
    SendMessageTypingAction, TypeChannelParticipant, TypeChat, TypeChatParticipant,
    TypeDocumentAttribute, TypeInputPeer, TypeMessageAction, TypeMessageEntity, TypePeer,
    TypePhotoSize, TypeUpdates, TypeUser, PhotoSize, TypeUserFull, UpdateChatUserTyping,
    UpdateNewChannelMessage, UpdateNewMessage, UpdateUserTyping, User, UserFull, MessageEntityPre,
    InputMediaUploadedDocument, InputPeerPhotoFileLocation)
from mautrix_appservice import MatrixRequestError, IntentError, AppService, IntentAPI

from .types import MatrixEventID, MatrixRoomID, MatrixUserID, TelegramID
from .context import Context
from .db import Portal as DBPortal, Message as DBMessage, TelegramFile as DBTelegramFile
from .util import ignore_coro, sane_mimetypes
from . import puppet as p, user as u, formatter, util

if TYPE_CHECKING:
    from .bot import Bot
    from .abstract_user import AbstractUser
    from .config import Config
    from .tgclient import MautrixTelegramClient

config = None  # type: Config

TypeMessage = Union[Message, MessageService]
TypeParticipant = Union[TypeChatParticipant, TypeChannelParticipant]
DedupMXID = Tuple[MatrixEventID, TelegramID]
InviteList = Union[MatrixUserID, List[MatrixUserID]]


class Portal:
    base_log = logging.getLogger("mau.portal")  # type: logging.Logger
    az = None  # type: AppService
    bot = None  # type: Bot
    loop = None  # type: asyncio.AbstractEventLoop

    # Config cache
    filter_mode = None  # type: str
    filter_list = None  # type: List[str]

    public_portals = False  # type: bool
    max_initial_member_sync = -1  # type: int
    sync_channel_members = True  # type: bool
    sync_matrix_state = True  # type: bool

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
                 local_config: Optional[str] = None, db_instance: DBPortal = None) -> None:
        self.mxid = mxid  # type: Optional[MatrixRoomID]
        self.tgid = tgid  # type: TelegramID
        self.tg_receiver = tg_receiver or tgid  # type: TelegramID
        self.peer_type = peer_type  # type: str
        self.username = username  # type: str
        self.megagroup = megagroup  # type: bool
        self.title = title  # type: Optional[str]
        self.about = about  # type: str
        self.photo_id = photo_id  # type: str
        self.local_config = json.loads(local_config or "{}")  # type: Dict[str, Any]
        self._db_instance = db_instance  # type: DBPortal
        self.deleted = False  # type: bool
        self.log = self.base_log.getChild(self.tgid_log) if self.tgid else self.base_log

        self._main_intent = None  # type: IntentAPI
        self._room_create_lock = asyncio.Lock()  # type: asyncio.Lock
        self._temp_pinned_message_id = None  # type: Optional[int]
        self._temp_pinned_message_id_space = None  # type: Optional[TelegramID]
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

    async def can_user_perform(self, user: 'u.User', event: str, default: int = 50) -> bool:
        if user.is_admin:
            return True
        if not self.mxid:
            # No room for anybody to perform actions in
            return False
        try:
            await self.main_intent.get_power_levels(self.mxid)
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

    def get_input_entity(self, user: 'AbstractUser') -> Awaitable[TypeInputPeer]:
        return user.client.get_input_entity(self.peer)

    async def get_entity(self, user: 'AbstractUser') -> TypeChat:
        try:
            return await user.client.get_entity(self.peer)
        except ValueError:
            if user.is_bot:
                self.log.warning(f"Could not find entity with bot {user.tgid}. "
                                 "Failing...")
                raise
            self.log.warning(f"Could not find entity with user {user.tgid}. "
                             "falling back to get_dialogs.")
            async for dialog in user.client.iter_dialogs():
                if dialog.entity.id == self.tgid:
                    return dialog.entity
            raise

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
        if self.sync_matrix_state:
            await self.sync_matrix_members()

    async def create_matrix_room(self, user: 'AbstractUser', entity: TypeChat = None,
                                 invites: InviteList = None, update_if_exists: bool = True,
                                 synchronous: bool = False) -> Optional[str]:
        if self.mxid:
            if update_if_exists:
                if not entity:
                    entity = await self.get_entity(user)
                update = self.update_matrix_room(user, entity, self.peer_type == "user")
                if synchronous:
                    await update
                else:
                    ignore_coro(asyncio.ensure_future(update, loop=self.loop))
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
            entity = await self.get_entity(user)
            self.log.debug("Fetched data: %s", entity)

        self.log.debug(f"Creating room")

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
        if config["appservice.community_id"]:
            initial_state.append({
                "type": "m.room.related_groups",
                "content": {"groups": [config["appservice.community_id"]]},
            })

        room_id = await self.main_intent.create_room(alias=alias, is_public=public,
                                                     is_direct=direct, invitees=invites or [],
                                                     name=self.title, initial_state=initial_state)
        if not room_id:
            raise Exception(f"Failed to create room")

        self.mxid = MatrixRoomID(room_id)
        self.by_mxid[self.mxid] = self
        self.save()
        self.az.state_store.set_power_levels(self.mxid, power_levels)
        user.register_portal(self)
        ignore_coro(asyncio.ensure_future(self.update_matrix_room(user, entity, direct, puppet,
                                                                  levels=power_levels, users=users,
                                                                  participants=participants),
                                          loop=self.loop))

        return self.mxid

    def _get_base_power_levels(self, levels: dict = None, entity: TypeChat = None) -> dict:
        levels = levels or {}
        if self.peer_type == "user":
            levels["ban"] = 100
            levels["kick"] = 100
            levels["invite"] = 100
            levels.setdefault("events", {})
            levels["events"]["m.room.name"] = 0
            levels["events"]["m.room.avatar"] = 0
            levels["events"]["m.room.topic"] = 0
            levels["state_default"] = 0
            levels["users_default"] = 0
            levels["events_default"] = 0
        else:
            dbr = entity.default_banned_rights
            if not dbr:
                self.log.debug(f"default_banned_rights is None in {entity}")
                dbr = ChatBannedRights(invite_users=True, change_info=True, pin_messages=True,
                                       send_stickers=False, send_messages=False, until_date=0)
            levels["ban"] = 99
            levels["kick"] = 50
            levels["invite"] = 50 if dbr.invite_users else 0
            levels.setdefault("events", {})
            levels["events"]["m.room.name"] = 50 if dbr.change_info else 0
            levels["events"]["m.room.avatar"] = 50 if dbr.change_info else 0
            levels["events"]["m.room.topic"] = 50 if dbr.change_info else 0
            levels["events"][
                "m.room.pinned_events"] = 50 if dbr.pin_messages else 0
            levels["events"]["m.room.power_levels"] = 75
            levels["events"]["m.room.history_visibility"] = 75
            levels["state_default"] = 50
            levels["users_default"] = 0
            levels["events_default"] = (50 if (self.peer_type == "channel" and not entity.megagroup
                                               or entity.default_banned_rights.send_messages)
                                        else 0)
            levels["events"]["m.sticker"] = 50 if dbr.send_stickers else levels["events_default"]
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

        user = u.User.get_by_tgid(TelegramID(bot.id))
        if user and user.is_bot:
            user.register_portal(self)

    async def sync_telegram_users(self, source: 'AbstractUser', users: List[User]) -> None:
        allowed_tgids = set()
        skip_deleted = config["bridge.skip_deleted_members"]
        for entity in users:
            if skip_deleted and entity.deleted:
                continue
            puppet = p.Puppet.get(TelegramID(entity.id))
            if entity.bot:
                self.add_bot_chat(entity)
            allowed_tgids.add(entity.id)
            await puppet.intent.ensure_joined(self.mxid)
            await puppet.update_info(source, entity)

            user = u.User.get_by_tgid(TelegramID(entity.id))
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

    async def update_info(self, user: 'AbstractUser', entity: TypeChat = None) -> None:
        if self.peer_type == "user":
            self.log.warning(f"Called update_info() for direct chat portal")
            return

        self.log.debug(f"Updating info")
        if not entity:
            entity = await self.get_entity(user)
            self.log.debug("Fetched data: %s", entity)
        changed = False

        if self.peer_type == "channel":
            changed = await self.update_username(entity.username) or changed
            # TODO update about text
            # changed = self.update_about(entity.about) or changed

        changed = await self.update_title(entity.title) or changed

        if isinstance(entity.photo, ChatPhoto):
            changed = await self.update_avatar(user, entity.photo) or changed

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
    def _get_largest_photo_size(photo: Union[Photo, Document]
                                ) -> Tuple[Optional[InputPhotoFileLocation],
                                           Optional[TypePhotoSize]]:
        if not photo:
            return None, None
        if isinstance(photo, Document) and not photo.thumbs:
            return None, None
        largest = max(photo.sizes if isinstance(photo, Photo) else photo.thumbs,
                      key=(lambda photo2: (len(photo2.bytes)
                                           if not isinstance(photo2, PhotoSize)
                                           else photo2.size)))
        return InputPhotoFileLocation(
            id=photo.id,
            access_hash=photo.access_hash,
            file_reference=photo.file_reference,
            thumb_size=largest.type,
        ), largest

    async def remove_avatar(self, _: 'AbstractUser', save: bool = False) -> None:
        await self.main_intent.set_room_avatar(self.mxid, None)
        self.photo_id = None
        if save:
            self.save()

    async def update_avatar(self, user: 'AbstractUser',
                            photo: Union[ChatPhoto, ChatPhotoEmpty, Photo, PhotoEmpty],
                            save: bool = False) -> bool:
        if isinstance(photo, ChatPhoto):
            loc = InputPeerPhotoFileLocation(
                peer=await self.get_input_entity(user),
                local_id=photo.photo_big.local_id,
                volume_id=photo.photo_big.volume_id,
                big=True
            )
            photo_id = f"{loc.volume_id}-{loc.local_id}"
        elif isinstance(photo, Photo):
            loc, largest = self._get_largest_photo_size(photo)
            photo_id = f"{largest.location.volume_id}-{largest.location.local_id}"
        elif isinstance(photo, (ChatPhotoEmpty, PhotoEmpty)):
            photo_id = ""
            loc = None
        else:
            raise ValueError(f"Unknown photo type {type(photo)}")
        if self.photo_id != photo_id:
            if not photo_id:
                await self.main_intent.set_room_avatar(self.mxid, "")
                self.photo_id = ""
                if save:
                    self.save()
                return True
            file = await util.transfer_file_to_matrix(user.client, self.main_intent, loc)
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
                    users = []  # type: List[TypeUser]
                    participants = []  # type: List[TypeParticipant]
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
        if self.username:
            return f"https://t.me/{self.username}"
        link = await user.client(ExportChatInviteRequest(peer=await self.get_input_entity(user)))
        if isinstance(link, ChatInviteEmpty):
            raise ValueError("Failed to get invite link.")
        return link.link

    async def get_authenticated_matrix_users(self) -> List['u.User']:
        try:
            members = await self.main_intent.get_room_members(self.mxid)
        except MatrixRequestError:
            return []
        authenticated = []  # type: List[u.User]
        has_bot = self.has_bot
        for member_str in members:
            member = MatrixUserID(member_str)
            if p.Puppet.get_id_from_mxid(member) or member == self.main_intent.mxid:
                continue
            user = await u.User.get_by_mxid(member).ensure_started()  # type: u.User
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
        if mime:
            return f"matrix_upload{sane_mimetypes.guess_extension(mime)}"
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
                        displayname=escape_html(displayname))
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

    async def sync_matrix_members(self) -> None:
        resp = await self.main_intent.get_room_joined_memberships(self.mxid)
        members = resp["joined"]
        for mxid, info in members.items():
            member = {
                "membership": "join",
            }
            if "display_name" in info:
                member["displayname"] = info["display_name"]
            if "avatar_url" in info:
                member["avatar_url"] = info["avatar_url"]
            self.az.state_store.set_member(self.mxid, mxid, member)

    def set_typing(self, user: 'u.User', typing: bool = True,
                   action: type = SendMessageTypingAction) -> Awaitable[bool]:
        return user.client(SetTypingRequest(
            self.peer, action() if typing else SendMessageCancelAction()))

    async def mark_read(self, user: 'u.User', event_id: MatrixEventID) -> None:
        if user.is_bot:
            return
        space = self.tgid if self.peer_type == "channel" else user.tgid
        message = DBMessage.get_by_mxid(event_id, self.mxid, space)
        if not message:
            return
        if self.peer_type == "channel":
            await user.client(ReadChannelHistoryRequest(
                channel=await self.get_input_entity(user), max_id=message.tgid))
        else:
            await user.client(ReadMessageHistoryRequest(peer=self.peer, max_id=message.tgid))

    async def kick_matrix(self, user: Union['u.User', 'p.Puppet'], source: 'u.User') -> None:
        if user.tgid == source.tgid:
            return
        if await source.needs_relaybot(self):
            source = self.bot
        if self.peer_type == "chat":
            await source.client(DeleteChatUserRequest(chat_id=self.tgid, user_id=user.tgid))
        elif self.peer_type == "channel":
            channel = await self.get_input_entity(source)
            rights = ChatBannedRights(datetime.fromtimestamp(0), True)
            await source.client(EditBannedRequest(channel=channel,
                                                  user_id=user.tgid,
                                                  banned_rights=rights))

    async def leave_matrix(self, user: 'u.User', source: 'u.User',
                           event_id: MatrixEventID) -> None:
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
            await self.kick_matrix(user, source)
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
            message["formatted_body"] = escape_html(message.get("body", "")).replace("\n", "<br/>")
        body = message["formatted_body"]

        tpl = (self.get_config(f"message_formats.[{msgtype}]")
               or "<b>$sender_displayname</b>: $message")
        displayname = await self.get_displayname(sender)
        tpl_args = dict(sender_mxid=sender.mxid,
                        sender_username=sender.mxid_localpart,
                        sender_displayname=escape_html(displayname),
                        message=body)
        message["formatted_body"] = Template(tpl).safe_substitute(tpl_args)

    async def _pre_process_matrix_message(self, sender: 'u.User', use_relaybot: bool,
                                          message: Dict[str, Any]) -> None:
        msgtype = message.get("msgtype", "m.text")
        if msgtype == "m.emote":
            await self._apply_msg_format(sender, msgtype, message)
            if "m.new_content" in message:
                await self._apply_msg_format(sender, msgtype, message["m.new_content"])
                message["m.new_content"]["msgtype"] = "m.text"
            message["msgtype"] = "m.text"
        elif use_relaybot:
            await self._apply_msg_format(sender, msgtype, message)
            if "m.new_content" in message:
                await self._apply_msg_format(sender, msgtype, message["m.new_content"])

    @staticmethod
    def _matrix_event_to_entities(event: Dict[str, Any]
                                  ) -> Tuple[str, Optional[List[TypeMessageEntity]]]:
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
                                  space: TelegramID, client: 'MautrixTelegramClient',
                                  message: Dict, reply_to: TelegramID) -> None:
        lock = self.require_send_lock(sender_id)
        async with lock:
            lp = self.get_config("telegram_link_preview")
            relates_to = message.get("m.relates_to", None) or {}
            if relates_to.get("rel_type", None) == "m.replace":
                orig_msg = DBMessage.get_by_mxid(relates_to.get("event_id", ""), self.mxid, space)
                if orig_msg:
                    response = await client.edit_message(self.peer, orig_msg.tgid,
                                                         message.get("m.new_content", message),
                                                         parse_mode=self._matrix_event_to_entities,
                                                         link_preview=lp)
                    self._add_telegram_message_to_db(event_id, space, -1, response)
                    return
            response = await client.send_message(self.peer, message, reply_to=reply_to,
                                                 parse_mode=self._matrix_event_to_entities,
                                                 link_preview=lp)
            self._add_telegram_message_to_db(event_id, space, 0, response)

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

        media = await client.upload_file_direct(
            file, mime, attributes, file_name,
            max_image_size=config["bridge.image_as_file_size"] * 1000 ** 2)
        lock = self.require_send_lock(sender_id)
        async with lock:
            relates_to = message.get("m.relates_to", None) or {}
            if relates_to.get("rel_type", None) == "m.replace":
                orig_msg = DBMessage.get_by_mxid(relates_to.get("event_id", ""), self.mxid, space)
                if orig_msg:
                    response = await client.edit_message(self.peer, orig_msg.tgid,
                                                         caption, file=media)
                    self._add_telegram_message_to_db(event_id, space, -1, response)
                    return
            try:
                response = await client.send_media(self.peer, media, reply_to=reply_to,
                                                   caption=caption)
            except (PhotoInvalidDimensionsError, PhotoSaveFileInvalidError, PhotoExtInvalidError):
                media = InputMediaUploadedDocument(file=media.file, mime_type=mime,
                                                   attributes=attributes)
                response = await client.send_media(self.peer, media, reply_to=reply_to,
                                                   caption=caption)
            self._add_telegram_message_to_db(event_id, space, 0, response)

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
            relates_to = message.get("m.relates_to", None) or {}
            if relates_to.get("rel_type", None) == "m.replace":
                orig_msg = DBMessage.get_by_mxid(relates_to.get("event_id", ""), self.mxid, space)
                if orig_msg:
                    response = await client.edit_message(self.peer, orig_msg.tgid,
                                                         caption, file=media)
                    self._add_telegram_message_to_db(event_id, space, -1, response)
                    return
            response = await client.send_media(self.peer, media, reply_to=reply_to,
                                               caption=caption, entities=entities)
            self._add_telegram_message_to_db(event_id, space, 0, response)

    def _add_telegram_message_to_db(self, event_id: MatrixEventID, space: TelegramID,
                                    edit_index: int, response: TypeMessage) -> None:
        self.log.debug("Handled Matrix message: %s", response)
        self.is_duplicate(response, (event_id, space), force_hash=edit_index != 0)
        if edit_index < 0:
            prev_edit = DBMessage.get_one_by_tgid(TelegramID(response.id), space, -1)
            edit_index = prev_edit.edit_index + 1
        DBMessage(
            tgid=TelegramID(response.id),
            tg_space=space,
            mx_room=self.mxid,
            mxid=event_id,
            edit_index=edit_index).insert()

    async def handle_matrix_message(self, sender: 'u.User', message: Dict[str, Any],
                                    event_id: MatrixEventID) -> None:
        if "body" not in message or "msgtype" not in message:
            self.log.debug(f"Ignoring message {event_id} in {self.mxid} without body or msgtype")
            return

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
        if self.peer_type != "chat" and self.peer_type != "channel":
            return
        try:
            if not pinned_message:
                await sender.client(UpdatePinnedMessageRequest(peer=self.peer, id=0))
            else:
                tg_space = self.tgid if self.peer_type == "channel" else sender.tgid
                message = DBMessage.get_by_mxid(pinned_message, self.mxid, tg_space)
                if message is None:
                    self.log.warning(f"Could not find pinned {pinned_message} in {self.mxid}")
                    return
                await sender.client(UpdatePinnedMessageRequest(peer=self.peer, id=message.tgid))
        except ChatNotModifiedError:
            pass

    async def handle_matrix_deletion(self, deleter: 'u.User', event_id: MatrixEventID) -> None:
        real_deleter = deleter if not await deleter.needs_relaybot(self) else self.bot
        space = self.tgid if self.peer_type == "channel" else real_deleter.tgid
        message = DBMessage.get_by_mxid(event_id, self.mxid, space)
        if not message:
            return
        if message.edit_index == 0:
            await real_deleter.client.delete_messages(self.peer, [message.tgid])
        else:
            self.log.debug(f"Ignoring deletion of edit event {message.mxid} in {message.mx_room}")

    async def _update_telegram_power_level(self, sender: 'u.User', user_id: TelegramID,
                                           level: int) -> None:
        if self.peer_type == "chat":
            await sender.client(EditChatAdminRequest(
                chat_id=self.tgid, user_id=user_id, is_admin=level >= 50))
        elif self.peer_type == "channel":
            moderator = level >= 50
            admin = level >= 75
            rights = ChatAdminRights(change_info=moderator, post_messages=moderator,
                                     edit_messages=moderator, delete_messages=moderator,
                                     ban_users=moderator, invite_users=moderator,
                                     pin_messages=moderator, add_admins=admin)
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
        if self.peer_type not in ("chat", "channel"):
            return
        peer = await self.get_input_entity(sender)
        await sender.client(EditChatAboutRequest(peer=peer, about=about))
        self.about = about
        self.save()

    async def handle_matrix_title(self, sender: 'u.User', title: str) -> None:
        if self.peer_type not in ("chat", "channel"):
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
        if self.peer_type not in ("chat", "channel"):
            # Invalid peer type
            return

        file = await self.main_intent.download_file(url)
        mime = magic.from_buffer(file, mime=True)
        ext = sane_mimetypes.guess_extension(mime)
        uploaded = await sender.client.upload_file(file, file_name=f"avatar{ext}", use_cache=False)
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
                loc, size = self._get_largest_photo_size(update.message.action.photo)
                self.photo_id = f"{size.location.volume_id}-{size.location.local_id}"
                self.save()
                break

    async def handle_matrix_upgrade(self, new_room: MatrixRoomID) -> None:
        old_room = self.mxid
        self.migrate_and_save_matrix(new_room)
        await self.main_intent.join_room(new_room)
        entity = None  # type: TypeInputPeer
        user = None  # type: AbstractUser
        if self.bot and self.has_bot:
            user = self.bot
            entity = await self.get_input_entity(self.bot)
        if not entity:
            user_mxids = await self.main_intent.get_room_members(self.mxid)
            for user_str in user_mxids:
                user_id = MatrixUserID(user_str)
                if user_id == self.az.bot_mxid:
                    continue
                user = u.User.get_by_mxid(user_id, create=False)
                if user and user.tgid:
                    entity = await self.get_input_entity(user)
                    if entity:
                        break
        if not entity:
            self.log.error(
                "Failed to fully migrate to upgraded Matrix room: no Telegram user found.")
            return
        users, participants = await self._get_users(self.bot, entity)
        await self.sync_telegram_users(user, users)
        levels = await self.main_intent.get_power_levels(self.mxid)
        await self.update_telegram_participants(participants, levels)
        self.log.info(f"Upgraded room from {old_room} to {self.mxid}")

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
        self.migrate_and_save_telegram(TelegramID(entity.id))
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
            if self.bot is not None:
                info, mxid = await self.bot.get_me()
                raise ValueError("Not enough Telegram users to create a chat. "
                                 "Invite more Telegram ghost users to the room, such as the "
                                 f"relaybot ([{info.first_name}](https://matrix.to/#/{mxid})).")
            raise ValueError("Not enough Telegram users to create a chat. "
                             "Invite more Telegram ghost users to the room.")
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
        self.db_instance.insert()
        self.log = self.base_log.getChild(str(self.tgid))

        if self.bot and self.bot.tgid in invites:
            self.bot.add_chat(self.tgid, self.peer_type)

        levels = await self.main_intent.get_power_levels(self.mxid)
        bot_level = self._get_bot_level(levels)
        if bot_level == 100:
            levels = self._get_base_power_levels(levels, entity)
            await self.main_intent.set_power_levels(self.mxid, levels)
        await self.handle_matrix_power_levels(source, levels["users"], {})

    async def invite_telegram(self, source: 'u.User',
                              puppet: Union[p.Puppet, 'AbstractUser']) -> None:
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
        elif self.peer_type != "user":
            return f"https://t.me/c/{self.tgid}/{evt.id}"
        return None

    async def handle_telegram_photo(self, source: 'AbstractUser', intent: IntentAPI, evt: Message,
                                    relates_to: Dict = None) -> Optional[Dict]:
        loc, largest_size = self._get_largest_photo_size(evt.media.photo)
        file = await util.transfer_file_to_matrix(source.client, intent, loc)
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
        name = f"image{sane_mimetypes.guess_extension(file.mime_type)}"
        await intent.set_typing(self.mxid, is_typing=False)
        result = await intent.send_image(self.mxid, file.mxc, info=info, text=name,
                                         relates_to=relates_to, timestamp=evt.date,
                                         external_url=self.get_external_url(evt))
        if evt.message:
            text, html, _ = await formatter.telegram_to_matrix(evt, source, self.main_intent,
                                                               no_reply_fallback=True)
            result = await intent.send_text(self.mxid, text, html=html, timestamp=evt.date,
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
    def _parse_telegram_document_meta(evt: Message, file: DBTelegramFile, attrs: Dict,
                                      thumb_size: TypePhotoSize) -> Tuple[Dict, str]:
        document = evt.media.document
        name = evt.message or attrs["name"]
        if attrs["is_sticker"]:
            alt = attrs["sticker_alt"]
            if len(alt) > 0:
                try:
                    name = f"{alt} ({unicodedata.name(alt[0]).lower()})"
                except ValueError:
                    name = alt

        generic_types = ("text/plain", "application/octet-stream")
        if file.mime_type in generic_types and document.mime_type not in generic_types:
            mime_type = document.mime_type or file.mime_type
        else:
            mime_type = file.mime_type or document.mime_type
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
                "h": file.thumbnail.height or thumb_size.h,
                "w": file.thumbnail.width or thumb_size.w,
                "size": file.thumbnail.size,
            }

        return info, name

    async def handle_telegram_document(self, source: 'AbstractUser', intent: IntentAPI,
                                       evt: Message, relates_to: dict = None) -> Optional[Dict]:
        document = evt.media.document

        attrs = self._parse_telegram_document_attributes(document.attributes)

        if document.size > config["bridge.max_document_size"] * 1000 ** 2:
            name = attrs["name"] or ""
            caption = f"\n{evt.message}" if evt.message else ""
            return await intent.send_notice(self.mxid, f"Too large file {name}{caption}")

        thumb_loc, thumb_size = self._get_largest_photo_size(document)
        if thumb_size and not isinstance(thumb_size, (PhotoSize, PhotoCachedSize)):
            self.log.debug(f"Unsupported thumbnail type {type(thumb_size)}")
            thumb_loc = None
            thumb_size = None
        file = await util.transfer_file_to_matrix(source.client, intent, document, thumb_loc,
                                                  is_sticker=attrs["is_sticker"])
        if not file:
            return None

        info, name = self._parse_telegram_document_meta(evt, file, attrs, thumb_size)

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

        if attrs["is_sticker"]:
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

    def handle_telegram_location(self, _: 'AbstractUser', intent: IntentAPI, evt: Message,
                                 relates_to: dict = None) -> Awaitable[dict]:
        location = evt.media.geo
        long = location.long
        lat = location.lat
        long_char = "E" if long > 0 else "W"
        lat_char = "N" if lat > 0 else "S"
        rounded_long = round(long, 5)
        rounded_lat = round(lat, 5)

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

    async def handle_telegram_text(self, source: 'AbstractUser', intent: IntentAPI, is_bot: bool,
                                   evt: Message) -> dict:
        self.log.debug(f"Sending {evt.message} to {self.mxid} by {intent.mxid}")
        text, html, relates_to = await formatter.telegram_to_matrix(evt, source, self.main_intent)
        await intent.set_typing(self.mxid, is_typing=False)
        msgtype = "m.notice" if is_bot and self.get_config("bot_messages_as_notices") else "m.text"
        return await intent.send_text(self.mxid, text, html=html, relates_to=relates_to,
                                      msgtype=msgtype, timestamp=evt.date,
                                      external_url=self.get_external_url(evt))

    async def handle_telegram_unsupported(self, source: 'AbstractUser', intent: IntentAPI,
                                          evt: Message, relates_to: dict = None) -> dict:
        override_text = ("This message is not supported on your version of Mautrix-Telegram. "
                         "Please check https://github.com/tulir/mautrix-telegram or ask your "
                         "bridge administrator about possible updates.")
        text, html, relates_to = await formatter.telegram_to_matrix(
            evt, source, self.main_intent, override_text=override_text)
        await intent.set_typing(self.mxid, is_typing=False)
        return await intent.send_message(self.mxid, {
            "body": text,
            "msgtype": "m.notice",
            "format": "org.matrix.custom.html",
            "formatted_body": html,
            "m.relates_to": relates_to,
            "net.maunium.telegram.unsupported": True,
        }, timestamp=evt.date, external_url=self.get_external_url(evt))

    async def handle_telegram_poll(self, source: 'AbstractUser', intent: IntentAPI, evt: Message,
                                   relates_to: dict) -> dict:
        poll = evt.media.poll  # type: Poll
        poll_id = self._encode_msgid(source, evt)

        _n = 0

        def n() -> int:
            nonlocal _n
            _n += 1
            return _n

        text = (f"Poll: {poll.question}\n"
                + "\n".join(f"{n()}. {answer.text}" for answer in poll.answers) +
                "\n"
                f"Vote with !tg vote {poll_id} <choice number>")

        html = (f"<strong>Poll</strong>: {poll.question}<br/>\n"
                f"<ol>"
                + "\n".join(f"<li>{answer.text}</li>"
                            for answer in poll.answers) +
                "</ol>\n"
                f"Vote with <code>!tg vote {poll_id} &lt;choice number&gt;</code>")
        await intent.set_typing(self.mxid, is_typing=False)
        return await intent.send_text(self.mxid, text, html=html, relates_to=relates_to,
                                      msgtype="m.text", timestamp=evt.date,
                                      external_url=self.get_external_url(evt))

    @staticmethod
    def _int_to_bytes(i: int) -> bytes:
        hex_value = "{0:010x}".format(i)
        return codecs.decode(hex_value, "hex_codec")

    def _encode_msgid(self, source: 'AbstractUser', evt: Message) -> str:
        if self.peer_type == "channel":
            play_id = (b"c"
                       + self._int_to_bytes(self.tgid)
                       + self._int_to_bytes(evt.id))
        elif self.peer_type == "chat":
            play_id = (b"g"
                       + self._int_to_bytes(self.tgid)
                       + self._int_to_bytes(evt.id)
                       + self._int_to_bytes(source.tgid))
        elif self.peer_type == "user":
            play_id = (b"u"
                       + self._int_to_bytes(self.tgid)
                       + self._int_to_bytes(evt.id))
        else:
            raise ValueError("Portal has invalid peer type")
        return base64.b64encode(play_id).decode("utf-8").rstrip("=")

    async def handle_telegram_game(self, source: 'AbstractUser', intent: IntentAPI,
                                   evt: Message, relates_to: dict = None):
        game = evt.media.game
        play_id = self._encode_msgid(source, evt)
        command = f"!tg play {play_id}"
        override_text = f"Run {command} in your bridge management room to play {game.title}"
        override_entities = [
            MessageEntityPre(offset=len("Run "), length=len(command), language="")]
        text, html, relates_to = await formatter.telegram_to_matrix(
            evt, source, self.main_intent,
            override_text=override_text, override_entities=override_entities)
        await intent.set_typing(self.mxid, is_typing=False)
        return await intent.send_message(self.mxid, {
            "body": text,
            "msgtype": "m.notice",
            "format": "org.matrix.custom.html",
            "formatted_body": html,
            "m.relates_to": relates_to,
            "net.maunium.telegram.game": play_id,
        }, timestamp=evt.date, external_url=self.get_external_url(evt))

    async def handle_telegram_edit(self, source: 'AbstractUser', sender: p.Puppet,
                                   evt: Message) -> None:
        if not self.mxid:
            return
        elif hasattr(evt, "media") and isinstance(evt.media, (MessageMediaGame,)):
            self.log.debug("Ignoring game message edit event")
            return

        lock = self.optional_send_lock(sender.tgid if sender else None)
        if lock:
            async with lock:
                pass

        tg_space = self.tgid if self.peer_type == "channel" else source.tgid

        temporary_identifier = MatrixEventID(
            f"${random.randint(1000000000000, 9999999999999)}TGBRIDGEDITEMP")
        duplicate_found = self.is_duplicate(evt, (temporary_identifier, tg_space), force_hash=True)
        if duplicate_found:
            mxid, other_tg_space = duplicate_found
            if tg_space != other_tg_space:
                prev_edit_msg = DBMessage.get_one_by_tgid(TelegramID(evt.id), tg_space, -1)
                if not prev_edit_msg:
                    return
                DBMessage(mxid=mxid, mx_room=self.mxid, tg_space=tg_space, tgid=evt.id,
                          edit_index=prev_edit_msg.edit_index + 1).insert()
            return

        text, html, _ = await formatter.telegram_to_matrix(evt, source, self.main_intent,
                                                           no_reply_fallback=True)
        editing_msg = DBMessage.get_one_by_tgid(TelegramID(evt.id), tg_space)
        if not editing_msg:
            self.log.info(f"Didn't find edited message {evt.id}@{tg_space} (src {source.tgid}) "
                          "in database.")
            return

        msgtype = ("m.notice"
                   if sender and sender.is_bot and self.get_config("bot_messages_as_notices")
                   else "m.text")
        content = {
            "body": f"Edit: {text}",
            "msgtype": msgtype,
            "format": "org.matrix.custom.html",
            "formatted_body": (f"<a href='https://matrix.to/#/{editing_msg.mx_room}/"
                               f"{editing_msg.mxid}'>Edit</a>: "
                               f"{html or escape_html(text)}"),
            "external_url": self.get_external_url(evt),
            "m.new_content": {
                "body": text,
                "msgtype": "m.text",
                **({"format": "org.matrix.custom.html",
                    "formatted_body": html} if html else {}),
            },
            "m.relates_to": {
                "rel_type": "m.replace",
                "event_id": editing_msg.mxid,
            },
        }

        intent = sender.intent if sender else self.main_intent
        await intent.set_typing(self.mxid, is_typing=False)
        response = await intent.send_message(self.mxid, content)
        mxid = response["event_id"]

        prev_edit_msg = DBMessage.get_one_by_tgid(TelegramID(evt.id), tg_space, -1) or editing_msg
        DBMessage(mxid=mxid, mx_room=self.mxid, tg_space=tg_space, tgid=evt.id,
                  edit_index=prev_edit_msg.edit_index + 1).insert()
        DBMessage.update_by_mxid(temporary_identifier, self.mxid, mxid=mxid)

    async def handle_telegram_message(self, source: 'AbstractUser', sender: p.Puppet,
                                      evt: Message) -> None:
        if not self.mxid:
            await self.create_matrix_room(source, invites=[source.mxid], update_if_exists=False)

        lock = self.optional_send_lock(sender.tgid if sender else None)
        if lock:
            async with lock:
                pass

        tg_space = self.tgid if self.peer_type == "channel" else source.tgid

        temporary_identifier = MatrixEventID(
            f"${random.randint(1000000000000, 9999999999999)}TGBRIDGETEMP")
        duplicate_found = self.is_duplicate(evt, (temporary_identifier, tg_space))
        if duplicate_found:
            mxid, other_tg_space = duplicate_found
            self.log.debug(f"Ignoring message {evt.id}@{tg_space} (src {source.tgid}) "
                           f"as it was already handled (in space {other_tg_space})")
            if tg_space != other_tg_space:
                DBMessage(tgid=TelegramID(evt.id), mx_room=self.mxid, mxid=mxid,
                          tg_space=tg_space, edit_index=0).insert()
            return

        if self.dedup_pre_db_check and self.peer_type == "channel":
            msg = DBMessage.get_one_by_tgid(TelegramID(evt.id), tg_space)
            if msg:
                self.log.debug(f"Ignoring message {evt.id} (src {source.tgid}) as it was already"
                               f"handled into {msg.mxid}. This duplicate was catched in the db "
                               "check. If you get this message often, consider increasing"
                               "bridge.deduplication.cache_queue_length in the config.")
                return

        if sender and not sender.displayname:
            self.log.debug(f"Telegram user {sender.tgid} sent a message, but doesn't have a "
                           "displayname, updating info...")
            entity = await source.client.get_entity(PeerUser(sender.tgid))
            await sender.update_info(source, entity)

        allowed_media = (MessageMediaPhoto, MessageMediaDocument, MessageMediaGeo,
                         MessageMediaGame, MessageMediaPoll, MessageMediaUnsupported)
        media = evt.media if hasattr(evt, "media") and isinstance(evt.media,
                                                                  allowed_media) else None
        intent = sender.intent if sender else self.main_intent
        if not media and evt.message:
            is_bot = sender.is_bot if sender else False
            response = await self.handle_telegram_text(source, intent, is_bot, evt)
        elif media:
            response = await {
                MessageMediaPhoto: self.handle_telegram_photo,
                MessageMediaDocument: self.handle_telegram_document,
                MessageMediaGeo: self.handle_telegram_location,
                MessageMediaPoll: self.handle_telegram_poll,
                MessageMediaUnsupported: self.handle_telegram_unsupported,
                MessageMediaGame: self.handle_telegram_game,
            }[type(media)](source, intent, evt,
                           relates_to=formatter.telegram_reply_to_matrix(evt, source))
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
            DBMessage(tgid=TelegramID(evt.id), mx_room=self.mxid, mxid=mxid,
                      tg_space=tg_space, edit_index=0).insert()
            DBMessage.update_by_mxid(temporary_identifier, self.mxid, mxid=mxid)
        except IntegrityError as e:
            self.log.exception(f"{e.__class__.__name__} while saving message mapping. "
                               "This might mean that an update was handled after it left the "
                               "dedup cache queue. You can try enabling bridge.deduplication."
                               "pre_db_check in the config.")
            await intent.redact(self.mxid, mxid)

    async def _create_room_on_action(self, source: 'AbstractUser',
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

    async def handle_telegram_action(self, source: 'AbstractUser', sender: p.Puppet,
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
            await self.update_avatar(source, action.photo, save=True)
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
            self.migrate_and_save_telegram(TelegramID(action.channel_id))
            await sender.intent.send_emote(self.mxid, "upgraded this group to a supergroup.")
        elif isinstance(action, MessageActionPinMessage):
            await self.receive_telegram_pin_sender(sender)
        elif isinstance(action, MessageActionGameScore):
            # TODO handle game score
            pass
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

        message = DBMessage.get_one_by_tgid(msg_id, self._temp_pinned_message_id_space)
        if message:
            await intent.set_pinned_messages(self.mxid, [message.mxid])
        else:
            await intent.set_pinned_messages(self.mxid, [])

    async def receive_telegram_pin_id(self, msg_id: int, receiver: TelegramID) -> None:
        if msg_id == 0:
            return await self.update_telegram_pin()
        self._temp_pinned_message_id = msg_id
        self._temp_pinned_message_id_space = receiver if self.peer_type != "channel" else self.tgid
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
            user = u.User.get_by_tgid(TelegramID(participant.user_id))
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

    def migrate_and_save_telegram(self, new_id: TelegramID) -> None:
        try:
            del self.by_tgid[self.tgid_full]
        except KeyError:
            pass
        try:
            existing = self.by_tgid[(new_id, new_id)]
            existing.delete()
        except KeyError:
            pass
        self.db_instance.update(tgid=new_id, tg_receiver=new_id, peer_type=self.peer_type)
        old_id = self.tgid
        self.tgid = new_id
        self.tg_receiver = new_id
        self.by_tgid[self.tgid_full] = self
        self.log = self.base_log.getChild(str(self.tgid))
        self.log.info(f"Telegram chat upgraded from {old_id}")

    def migrate_and_save_matrix(self, new_id: MatrixRoomID) -> None:
        try:
            del self.by_mxid[self.mxid]
        except KeyError:
            pass
        self.mxid = new_id
        self.db_instance.update(mxid=self.mxid)
        self.by_mxid[self.mxid] = self

    def save(self) -> None:
        self.db_instance.update(mxid=self.mxid, username=self.username, title=self.title,
                                about=self.about, photo_id=self.photo_id,
                                config=json.dumps(self.local_config))

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
            self._db_instance.delete()
        self.deleted = True

    @classmethod
    def from_db(cls, db_portal: DBPortal) -> 'Portal':
        return Portal(tgid=db_portal.tgid, tg_receiver=db_portal.tg_receiver,
                      peer_type=db_portal.peer_type, mxid=db_portal.mxid,
                      username=db_portal.username, megagroup=db_portal.megagroup,
                      title=db_portal.title, about=db_portal.about, photo_id=db_portal.photo_id,
                      local_config=db_portal.config, db_instance=db_portal)

    # endregion
    # region Class instance lookup

    @classmethod
    def get_by_mxid(cls, mxid: MatrixRoomID) -> Optional['Portal']:
        try:
            return cls.by_mxid[mxid]
        except KeyError:
            pass

        portal = DBPortal.get_by_mxid(mxid)
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

        dbportal = DBPortal.get_by_username(username)
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

        db_portal = DBPortal.get_by_tgid(tgid, tg_receiver)
        if db_portal:
            return cls.from_db(db_portal)

        if peer_type:
            portal = Portal(tgid, peer_type=peer_type, tg_receiver=tg_receiver)
            portal.db_instance.insert()
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
    Portal.az, config, Portal.loop, Portal.bot = context.core
    Portal.max_initial_member_sync = config["bridge.max_initial_member_sync"]
    Portal.sync_channel_members = config["bridge.sync_channel_members"]
    Portal.sync_matrix_state = config["bridge.sync_matrix_state"]
    Portal.public_portals = config["bridge.public_portals"]
    Portal.filter_mode = config["bridge.filter.mode"]
    Portal.filter_list = config["bridge.filter.list"]
    Portal.dedup_pre_db_check = config["bridge.deduplication.pre_db_check"]
    Portal.dedup_cache_queue_length = config["bridge.deduplication.cache_queue_length"]
    Portal.alias_template = config.get("bridge.alias_template", "telegram_{groupname}")
    Portal.hs_domain = config["homeserver.domain"]
    Portal.mx_alias_regex = re.compile(
        f"#{Portal.alias_template.format(groupname='(.+)')}:{Portal.hs_domain}")
