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
from typing import Awaitable, Dict, List, Optional, Tuple, Union, Any, Set, Iterable, TYPE_CHECKING
from abc import ABC, abstractmethod
import asyncio
import logging
import json

from telethon.tl.functions.messages import ExportChatInviteRequest
from telethon.tl.types import (Channel, ChannelFull, Chat, ChatFull, ChatInviteEmpty, InputChannel,
                               InputPeerChannel, InputPeerChat, InputPeerUser, InputUser,
                               PeerChannel, PeerChat, PeerUser, TypeChat, TypeInputPeer, TypePeer,
                               TypeUser, TypeUserFull, User, UserFull, TypeInputChannel, Photo,
                               Document, TypePhotoSize, PhotoSize, InputPhotoFileLocation,
                               TypeChatParticipant, TypeChannelParticipant, PhotoEmpty, ChatPhoto,
                               ChatPhotoEmpty)

from mautrix.errors import MatrixRequestError, IntentError
from mautrix.appservice import AppService, IntentAPI
from mautrix.types import (RoomID, RoomAlias, UserID, EventID, EventType, MessageEventContent,
                           PowerLevelStateEventContent, ContentURI)
from mautrix.util.simple_template import SimpleTemplate
from mautrix.util.simple_lock import SimpleLock
from mautrix.util.logging import TraceLogger
from mautrix.bridge import BasePortal as MautrixBasePortal

from ..types import TelegramID
from ..context import Context
from ..db import Portal as DBPortal, Message as DBMessage
from .. import puppet as p, user as u, util
from .deduplication import PortalDedup
from .send_lock import PortalSendLock

if TYPE_CHECKING:
    from ..bot import Bot
    from ..abstract_user import AbstractUser
    from ..config import Config
    from ..matrix import MatrixHandler
    from . import Portal

TypeParticipant = Union[TypeChatParticipant, TypeChannelParticipant]
TypeChatPhoto = Union[ChatPhoto, ChatPhotoEmpty, Photo, PhotoEmpty]
InviteList = Union[UserID, List[UserID]]

config: Optional['Config'] = None


class BasePortal(MautrixBasePortal, ABC):
    base_log: TraceLogger = logging.getLogger("mau.portal")
    az: AppService = None
    bot: 'Bot' = None
    loop: asyncio.AbstractEventLoop = None
    matrix: 'MatrixHandler' = None

    # Config cache
    filter_mode: str = None
    filter_list: List[str] = None

    max_initial_member_sync: int = -1
    sync_channel_members: bool = True
    sync_matrix_state: bool = True
    public_portals: bool = False
    private_chat_portal_meta: bool = False

    alias_template: SimpleTemplate[str]
    hs_domain: str

    # Instance cache
    by_mxid: Dict[RoomID, 'Portal'] = {}
    by_tgid: Dict[Tuple[TelegramID, TelegramID], 'Portal'] = {}

    mxid: Optional[RoomID]
    tgid: TelegramID
    tg_receiver: TelegramID
    peer_type: str
    username: str
    megagroup: bool
    title: Optional[str]
    about: Optional[str]
    photo_id: Optional[str]
    local_config: Dict[str, Any]
    avatar_url: Optional[ContentURI]
    encrypted: bool
    deleted: bool
    backfill_lock: SimpleLock
    backfill_method_lock: asyncio.Lock
    backfill_leave: Optional[Set[IntentAPI]]
    log: TraceLogger

    alias: Optional[RoomAlias]

    dedup: PortalDedup
    send_lock: PortalSendLock

    _db_instance: DBPortal
    _main_intent: Optional[IntentAPI]
    _room_create_lock: asyncio.Lock

    def __init__(self, tgid: TelegramID, peer_type: str, tg_receiver: Optional[TelegramID] = None,
                 mxid: Optional[RoomID] = None, username: Optional[str] = None,
                 megagroup: Optional[bool] = False, title: Optional[str] = None,
                 about: Optional[str] = None, photo_id: Optional[str] = None,
                 local_config: Optional[str] = None, avatar_url: Optional[ContentURI] = None,
                 encrypted: Optional[bool] = False, db_instance: DBPortal = None) -> None:
        self.mxid = mxid
        self.tgid = tgid
        self.tg_receiver = tg_receiver or tgid
        self.peer_type = peer_type
        self.username = username
        self.megagroup = megagroup
        self.title = title
        self.about = about
        self.photo_id = photo_id
        self.local_config = json.loads(local_config or "{}")
        self.avatar_url = avatar_url
        self.encrypted = encrypted
        self._db_instance = db_instance
        self._main_intent = None
        self.deleted = False
        self.log = self.base_log.getChild(self.tgid_log if self.tgid else self.mxid)
        self.backfill_lock = SimpleLock("Waiting for backfilling to finish before handling %s",
                                        log=self.log)
        self.backfill_method_lock = asyncio.Lock()
        self.backfill_leave = None

        self.dedup = PortalDedup(self)
        self.send_lock = PortalSendLock()

        if tgid:
            self.by_tgid[self.tgid_full] = self
        if mxid:
            self.by_mxid[mxid] = self

    # region Properties

    @property
    def tgid_full(self) -> Tuple[TelegramID, TelegramID]:
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
    def alias(self) -> Optional[RoomAlias]:
        if not self.username:
            return None
        return RoomAlias(f"#{self.alias_localpart}:{self.hs_domain}")

    @property
    def alias_localpart(self) -> Optional[str]:
        if not self.username:
            return None
        return self.alias_template.format(self.username)

    @property
    def peer(self) -> Union[TypePeer, TypeInputPeer]:
        if self.peer_type == "user":
            return PeerUser(user_id=self.tgid)
        elif self.peer_type == "chat":
            return PeerChat(chat_id=self.tgid)
        elif self.peer_type == "channel":
            return PeerChannel(channel_id=self.tgid)

    @property
    def has_bot(self) -> bool:
        return (bool(self.bot)
                and (self.bot.is_in_chat(self.tgid)
                     or (self.peer_type == "user" and self.tg_receiver == self.bot.tgid)))

    @property
    def main_intent(self) -> IntentAPI:
        if not self._main_intent:
            direct = self.peer_type == "user"
            puppet = p.Puppet.get(self.tgid) if direct else None
            self._main_intent = puppet.intent_for(self) if direct else self.az.intent
        return self._main_intent

    @property
    def allow_bridging(self) -> bool:
        if self.peer_type == "user":
            return True
        elif self.filter_mode == "whitelist":
            return self.tgid in self.filter_list
        elif self.filter_mode == "blacklist":
            return self.tgid not in self.filter_list
        return True

    # endregion
    # region Miscellaneous getters

    def get_config(self, key: str) -> Any:
        local = util.recursive_get(self.local_config, key)
        if local is not None:
            return local
        return config[f"bridge.{key}"]

    @staticmethod
    def _get_largest_photo_size(photo: Union[Photo, Document]
                                ) -> Tuple[Optional[InputPhotoFileLocation],
                                           Optional[TypePhotoSize]]:
        if not photo or isinstance(photo, PhotoEmpty) or (isinstance(photo, Document)
                                                          and not photo.thumbs):
            return None, None

        largest = max(photo.thumbs if isinstance(photo, Document) else photo.sizes,
                      key=(lambda photo2: (len(photo2.bytes)
                                           if not isinstance(photo2, PhotoSize)
                                           else photo2.size)))
        return InputPhotoFileLocation(
            id=photo.id,
            access_hash=photo.access_hash,
            file_reference=photo.file_reference,
            thumb_size=largest.type,
        ), largest

    async def can_user_perform(self, user: 'u.User', event: str) -> bool:
        if user.is_admin:
            return True
        if not self.mxid:
            # No room for anybody to perform actions in
            return False
        try:
            await self.main_intent.get_power_levels(self.mxid)
        except MatrixRequestError:
            return False
        evt_type = EventType.find(f"net.maunium.telegram.{event}", t_class=EventType.Class.STATE)
        return await self.main_intent.state_store.has_power_level(self.mxid, user.mxid, evt_type)

    def get_input_entity(self, user: 'AbstractUser'
                         ) -> Awaitable[Union[TypeInputPeer, TypeInputChannel]]:
        return user.client.get_input_entity(self.peer)

    async def get_entity(self, user: 'AbstractUser') -> TypeChat:
        try:
            return await user.client.get_entity(self.peer)
        except ValueError:
            if user.is_bot:
                self.log.warning(f"Could not find entity with bot {user.tgid}. Failing...")
                raise
            self.log.warning(f"Could not find entity with user {user.tgid}. "
                             "falling back to get_dialogs.")
            async for dialog in user.client.iter_dialogs():
                if dialog.entity.id == self.tgid:
                    return dialog.entity
            raise

    async def get_invite_link(self, user: 'u.User') -> str:
        if self.peer_type == "user":
            raise ValueError("You can't invite users to private chats.")
        if self.username:
            return f"https://t.me/{self.username}"
        link = await user.client(ExportChatInviteRequest(peer=await self.get_input_entity(user)))
        if isinstance(link, ChatInviteEmpty):
            raise ValueError("Failed to get invite link.")
        return link.link

    # endregion
    # region Matrix room cleanup

    async def get_authenticated_matrix_users(self) -> List[UserID]:
        try:
            members = await self.main_intent.get_room_members(self.mxid)
        except MatrixRequestError:
            return []
        authenticated: List[UserID] = []
        has_bot = self.has_bot
        for member in members:
            if p.Puppet.get_id_from_mxid(member) or member == self.az.bot_mxid:
                continue
            user = await u.User.get_by_mxid(member).ensure_started()
            authenticated_through_bot = has_bot and user.relaybot_whitelisted
            if authenticated_through_bot or await user.has_full_access(allow_bot=True):
                authenticated.append(user.mxid)
        return authenticated

    async def cleanup_portal(self, message: str, puppets_only: bool = False, delete: bool = True
                             ) -> None:
        if self.username:
            try:
                await self.main_intent.remove_room_alias(self.alias_localpart)
            except (MatrixRequestError, IntentError):
                self.log.warning("Failed to remove alias when cleaning up room", exc_info=True)
        await self.cleanup_room(self.main_intent, self.mxid, message, puppets_only)
        if delete:
            await self.delete()

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
                        config=json.dumps(self.local_config), avatar_url=self.avatar_url,
                        encrypted=self.encrypted)

    async def save(self) -> None:
        self.db_instance.edit(mxid=self.mxid, username=self.username, title=self.title,
                              about=self.about, photo_id=self.photo_id, megagroup=self.megagroup,
                              config=json.dumps(self.local_config), avatar_url=self.avatar_url,
                              encrypted=self.encrypted)

    async def delete(self) -> None:
        self.delete_sync()

    def delete_sync(self) -> None:
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
        DBMessage.delete_all(self.mxid)
        self.deleted = True

    @classmethod
    def from_db(cls, db_portal: DBPortal) -> 'Portal':
        return cls(tgid=db_portal.tgid, tg_receiver=db_portal.tg_receiver,
                   peer_type=db_portal.peer_type, mxid=db_portal.mxid, username=db_portal.username,
                   megagroup=db_portal.megagroup, title=db_portal.title, about=db_portal.about,
                   photo_id=db_portal.photo_id, local_config=db_portal.config,
                   avatar_url=db_portal.avatar_url, encrypted=db_portal.encrypted,
                   db_instance=db_portal)

    # endregion
    # region Class instance lookup

    @classmethod
    def all(cls) -> Iterable['Portal']:
        for db_portal in DBPortal.all():
            try:
                yield cls.by_tgid[(db_portal.tgid, db_portal.tg_receiver)]
            except KeyError:
                yield cls.from_db(db_portal)

    @classmethod
    def get_by_mxid(cls, mxid: RoomID) -> Optional['Portal']:
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
        return cls.alias_template.parse(alias)

    @classmethod
    def find_by_username(cls, username: str) -> Optional['Portal']:
        if not username:
            return None

        username = username.lower()

        for _, portal in cls.by_tgid.items():
            if portal.username and portal.username.lower() == username:
                return portal

        dbportal = DBPortal.get_by_username(username)
        if dbportal:
            return cls.from_db(dbportal)

        return None

    @classmethod
    def get_by_tgid(cls, tgid: TelegramID, tg_receiver: Optional[TelegramID] = None,
                    peer_type: str = None) -> Optional['Portal']:
        if peer_type == "user" and tg_receiver is None:
            raise ValueError("tg_receiver is required when peer_type is \"user\"")
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
            cls.log.info(f"Creating portal for {peer_type} {tgid} (receiver {tg_receiver})")
            # TODO enable this for non-release builds
            #      (or add better wrong peer type error handling)
            # if peer_type == "chat":
            #     import traceback
            #     cls.log.info("Chat portal stack trace:\n" + "".join(traceback.format_stack()))
            portal = cls(tgid, peer_type=peer_type, tg_receiver=tg_receiver)
            portal.db_instance.insert()
            return portal

        return None

    @classmethod
    def get_by_entity(cls, entity: Union[TypeChat, TypePeer, TypeUser, TypeUserFull,
                                         TypeInputPeer],
                      receiver_id: Optional[TelegramID] = None, create: bool = True
                      ) -> Optional['Portal']:
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
        return cls.get_by_tgid(TelegramID(entity_id),
                               receiver_id if type_name == "user" else entity_id,
                               type_name if create else None)

    # endregion
    # region Abstract methods (cross-called in matrix/metadata/telegram classes)

    @abstractmethod
    async def update_matrix_room(self, user: 'AbstractUser', entity: Union[TypeChat, User],
                                 direct: bool, puppet: p.Puppet = None,
                                 levels: PowerLevelStateEventContent = None,
                                 users: List[User] = None,
                                 participants: List[TypeParticipant] = None) -> None:
        pass

    @abstractmethod
    async def create_matrix_room(self, user: 'AbstractUser', entity: TypeChat = None,
                                 invites: InviteList = None, update_if_exists: bool = True,
                                 synchronous: bool = False) -> Optional[str]:
        pass

    @abstractmethod
    async def _add_telegram_user(self, user_id: TelegramID, source: Optional['AbstractUser'] = None
                                 ) -> None:
        pass

    @abstractmethod
    async def _delete_telegram_user(self, user_id: TelegramID, sender: p.Puppet) -> None:
        pass

    @abstractmethod
    async def _update_title(self, title: str, sender: Optional['p.Puppet'] = None,
                            save: bool = False) -> bool:
        pass

    @abstractmethod
    async def _update_avatar(self, user: 'AbstractUser', photo: Union[TypeChatPhoto],
                             sender: Optional['p.Puppet'] = None, save: bool = False) -> bool:
        pass

    @abstractmethod
    def _migrate_and_save_telegram(self, new_id: TelegramID) -> None:
        pass

    @abstractmethod
    async def update_bridge_info(self) -> None:
        pass

    @abstractmethod
    def handle_matrix_power_levels(self, sender: 'u.User', new_levels: Dict[UserID, int],
                                   old_levels: Dict[UserID, int], event_id: Optional[EventID]
                                   ) -> Awaitable[None]:
        pass

    @abstractmethod
    def backfill(self, source: 'AbstractUser', is_initial: bool = False,
                 limit: Optional[int] = None, last_id: Optional[int] = None) -> Awaitable[None]:
        pass

    @abstractmethod
    async def _send_delivery_receipt(self, event_id: EventID, room_id: Optional[RoomID] = None
                                     ) -> None:
        pass

    # endregion


def init(context: Context) -> None:
    global config
    BasePortal.az, config, BasePortal.loop, BasePortal.bot = context.core
    BasePortal.matrix = context.mx
    MautrixBasePortal.bridge = context.bridge
    BasePortal.max_initial_member_sync = config["bridge.max_initial_member_sync"]
    BasePortal.sync_channel_members = config["bridge.sync_channel_members"]
    BasePortal.sync_matrix_state = config["bridge.sync_matrix_state"]
    BasePortal.public_portals = config["bridge.public_portals"]
    BasePortal.private_chat_portal_meta = config["bridge.private_chat_portal_meta"]
    BasePortal.filter_mode = config["bridge.filter.mode"]
    BasePortal.filter_list = config["bridge.filter.list"]
    BasePortal.hs_domain = config["homeserver.domain"]
    BasePortal.alias_template = SimpleTemplate(config["bridge.alias_template"], "groupname",
                                               prefix="#", suffix=f":{BasePortal.hs_domain}")
