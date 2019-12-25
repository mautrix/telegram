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
from typing import Awaitable, Any, Dict, Iterable, Optional, Union, TYPE_CHECKING
from difflib import SequenceMatcher
import unicodedata
import asyncio
import logging

from telethon.tl.types import (UserProfilePhoto, User, UpdateUserName, PeerUser, TypeInputPeer,
                               InputPeerPhotoFileLocation, UserProfilePhotoEmpty, TypeInputUser)

from mautrix.appservice import AppService, IntentAPI
from mautrix.errors import MatrixRequestError
from mautrix.bridge import CustomPuppetMixin
from mautrix.types import UserID, SyncToken
from mautrix.util.simple_template import SimpleTemplate

from .types import TelegramID
from .db import Puppet as DBPuppet
from . import util, portal as p

if TYPE_CHECKING:
    from .matrix import MatrixHandler
    from .config import Config
    from .context import Context
    from .abstract_user import AbstractUser

config: Optional['Config'] = None


class Puppet(CustomPuppetMixin):
    log: logging.Logger = logging.getLogger("mau.puppet")
    az: AppService
    mx: 'MatrixHandler'
    loop: asyncio.AbstractEventLoop
    hs_domain: str
    mxid_template: SimpleTemplate[TelegramID]
    displayname_template: SimpleTemplate[str]

    cache: Dict[TelegramID, 'Puppet'] = {}
    by_custom_mxid: Dict[UserID, 'Puppet'] = {}

    id: TelegramID
    access_token: Optional[str]
    custom_mxid: Optional[UserID]
    _next_batch: Optional[SyncToken]
    default_mxid: UserID

    username: Optional[str]
    displayname: Optional[str]
    displayname_source: Optional[TelegramID]
    photo_id: Optional[str]
    is_bot: bool
    is_registered: bool
    disable_updates: bool

    default_mxid_intent: IntentAPI
    intent: IntentAPI

    sync_task: Optional[asyncio.Future]

    _db_instance: Optional[DBPuppet]

    def __init__(self,
                 id: TelegramID,
                 access_token: Optional[str] = None,
                 custom_mxid: Optional[UserID] = None,
                 next_batch: Optional[SyncToken] = None,
                 username: Optional[str] = None,
                 displayname: Optional[str] = None,
                 displayname_source: Optional[TelegramID] = None,
                 photo_id: Optional[str] = None,
                 is_bot: bool = False,
                 is_registered: bool = False,
                 disable_updates: bool = False,
                 db_instance: Optional[DBPuppet] = None) -> None:
        self.id = id
        self.access_token = access_token
        self.custom_mxid = custom_mxid
        self._next_batch = next_batch
        self.default_mxid = self.get_mxid_from_id(self.id)

        self.username = username
        self.displayname = displayname
        self.displayname_source = displayname_source
        self.photo_id = photo_id
        self.is_bot = is_bot
        self.is_registered = is_registered
        self.disable_updates = disable_updates
        self._db_instance = db_instance

        self.default_mxid_intent = self.az.intent.user(self.default_mxid)
        self.intent = self._fresh_intent()
        self.sync_task = None

        self.cache[id] = self
        if self.custom_mxid:
            self.by_custom_mxid[self.custom_mxid] = self

        self.log = self.log.getChild(str(self.id))

    @property
    def tgid(self) -> TelegramID:
        return self.id

    @property
    def peer(self) -> PeerUser:
        return PeerUser(user_id=self.tgid)

    @property
    def next_batch(self) -> SyncToken:
        return self._next_batch

    @next_batch.setter
    def next_batch(self, value: SyncToken) -> None:
        self._next_batch = value
        self.db_instance.edit(next_batch=self._next_batch)

    @staticmethod
    async def is_logged_in() -> bool:
        """ Is True if the puppet is logged in. """
        return True

    @property
    def plain_displayname(self) -> str:
        return self.displayname_template.parse(self.displayname) or self.displayname

    def get_input_entity(self, user: 'AbstractUser'
                         ) -> Awaitable[Union[TypeInputPeer, TypeInputUser]]:
        return user.client.get_input_entity(self.peer)

    def intent_for(self, portal: 'p.Portal') -> IntentAPI:
        if portal.tgid == self.tgid:
            return self.default_mxid_intent
        return self.intent

    # region DB conversion

    @property
    def db_instance(self) -> DBPuppet:
        if not self._db_instance:
            self._db_instance = self.new_db_instance()
        return self._db_instance

    @property
    def _fields(self) -> Dict[str, Any]:
        return dict(access_token=self.access_token, next_batch=self._next_batch,
                    custom_mxid=self.custom_mxid, username=self.username, is_bot=self.is_bot,
                    displayname=self.displayname, displayname_source=self.displayname_source,
                    photo_id=self.photo_id, matrix_registered=self.is_registered,
                    disable_updates=self.disable_updates)

    def new_db_instance(self) -> DBPuppet:
        return DBPuppet(id=self.id, **self._fields)

    def save(self) -> None:
        self.db_instance.edit(**self._fields)

    @classmethod
    def from_db(cls, db_puppet: DBPuppet) -> 'Puppet':
        return Puppet(db_puppet.id, db_puppet.access_token, db_puppet.custom_mxid,
                      db_puppet.next_batch, db_puppet.username, db_puppet.displayname,
                      db_puppet.displayname_source, db_puppet.photo_id, db_puppet.is_bot,
                      db_puppet.matrix_registered, db_puppet.disable_updates,
                      db_instance=db_puppet)

    # endregion
    # region Info updating

    def similarity(self, query: str) -> int:
        username_similarity = (SequenceMatcher(None, self.username, query).ratio()
                               if self.username else 0)
        displayname_similarity = (SequenceMatcher(None, self.displayname, query).ratio()
                                  if self.displayname else 0)
        similarity = max(username_similarity, displayname_similarity)
        return int(round(similarity * 100))

    @staticmethod
    def _filter_name(name: str) -> str:
        if not name:
            return ""
        whitespace = ("\t\n\r\v\f \u00a0\u034f\u180e\u2063\u202f\u205f\u2800\u3000\u3164\ufeff"
                      "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u200b"
                      "\u200c\u200d\u200e\u200f\ufe0f")
        name = "".join(c for c in name.strip(whitespace) if unicodedata.category(c) != 'Cf')
        return name

    @classmethod
    def get_displayname(cls, info: User, enable_format: bool = True) -> str:
        fn = cls._filter_name(info.first_name)
        ln = cls._filter_name(info.last_name)
        data = {
            "phone number": info.phone if hasattr(info, "phone") else None,
            "username": info.username,
            "full name": " ".join([fn, ln]).strip(),
            "full name reversed": " ".join([ln, fn]).strip(),
            "first name": fn,
            "last name": ln,
        }
        preferences = config["bridge.displayname_preference"]
        name = None
        for preference in preferences:
            name = data[preference]
            if name:
                break

        if isinstance(info, User) and info.deleted:
            name = f"Deleted account {info.id}"
        elif not name:
            name = str(info.id)

        if not enable_format:
            return name
        return cls.displayname_template.format_full(name)

    async def try_update_info(self, source: 'AbstractUser', info: User) -> None:
        try:
            await self.update_info(source, info)
        except Exception:
            source.log.exception(f"Failed to update info of {self.tgid}")

    async def update_info(self, source: 'AbstractUser', info: User) -> None:
        if self.disable_updates:
            return
        changed = False
        if self.username != info.username:
            self.username = info.username
            changed = True

        try:
            changed = await self.update_displayname(source, info) or changed
            if isinstance(info.photo, UserProfilePhoto):
                changed = await self.update_avatar(source, info.photo) or changed
        except Exception:
            self.log.exception(f"Failed to update info from source {source.tgid}")

        self.is_bot = info.bot

        if changed:
            self.save()

    async def update_displayname(self, source: 'AbstractUser', info: Union[User, UpdateUserName]
                                 ) -> bool:
        if self.disable_updates:
            return False
        allow_source = (source.is_relaybot
                        or self.displayname_source == source.tgid
                        # User is not a contact, so there's no custom name
                        or not info.contact
                        # No displayname source, so just trust anything
                        or self.displayname_source is None)
        if not allow_source:
            return False
        elif isinstance(info, UpdateUserName):
            info = await source.client.get_entity(PeerUser(self.tgid))

        displayname = self.get_displayname(info)
        if displayname != self.displayname:
            self.displayname = displayname
            self.displayname_source = source.tgid
            try:
                await self.default_mxid_intent.set_displayname(
                    displayname[:config["bridge.displayname_max_length"]])
            except MatrixRequestError:
                self.log.exception("Failed to set displayname")
                self.displayname = ""
                self.displayname_source = None
            return True
        elif source.is_relaybot or self.displayname_source is None:
            self.displayname_source = source.tgid
            return True
        return False

    async def update_avatar(self, source: 'AbstractUser',
                            photo: Union[UserProfilePhoto, UserProfilePhotoEmpty]) -> bool:
        if self.disable_updates:
            return False

        if isinstance(photo, UserProfilePhotoEmpty):
            photo_id = ""
        else:
            photo_id = str(photo.photo_id)
        if self.photo_id != photo_id:
            if not photo_id:
                self.photo_id = ""
                try:
                    await self.default_mxid_intent.set_avatar_url("")
                except MatrixRequestError:
                    self.log.exception("Failed to set avatar")
                    self.photo_id = ""
                return True

            loc = InputPeerPhotoFileLocation(
                peer=await self.get_input_entity(source),
                local_id=photo.photo_big.local_id,
                volume_id=photo.photo_big.volume_id,
                big=True
            )
            file = await util.transfer_file_to_matrix(source.client, self.default_mxid_intent, loc)
            if file:
                self.photo_id = photo_id
                try:
                    await self.default_mxid_intent.set_avatar_url(file.mxc)
                except MatrixRequestError:
                    self.log.exception("Failed to set avatar")
                    self.photo_id = ""
                return True
        return False

    # endregion
    # region Getters

    @classmethod
    def get(cls, tgid: TelegramID, create: bool = True) -> Optional['Puppet']:
        try:
            return cls.cache[tgid]
        except KeyError:
            pass

        puppet = DBPuppet.get_by_tgid(tgid)
        if puppet:
            return cls.from_db(puppet)

        if create:
            puppet = cls(tgid)
            puppet.db_instance.insert()
            return puppet

        return None

    @classmethod
    def get_by_mxid(cls, mxid: UserID, create: bool = True) -> Optional['Puppet']:
        tgid = cls.get_id_from_mxid(mxid)
        if tgid:
            return cls.get(tgid, create)

        return None

    @classmethod
    def get_by_custom_mxid(cls, mxid: UserID) -> Optional['Puppet']:
        if not mxid:
            raise ValueError("Matrix ID can't be empty")

        try:
            return cls.by_custom_mxid[mxid]
        except KeyError:
            pass

        puppet = DBPuppet.get_by_custom_mxid(mxid)
        if puppet:
            puppet = cls.from_db(puppet)
            return puppet

        return None

    @classmethod
    def all_with_custom_mxid(cls) -> Iterable['Puppet']:
        return (cls.by_custom_mxid[puppet.mxid]
                if puppet.custom_mxid in cls.by_custom_mxid
                else cls.from_db(puppet)
                for puppet in DBPuppet.all_with_custom_mxid())

    @classmethod
    def get_id_from_mxid(cls, mxid: UserID) -> Optional[TelegramID]:
        return cls.mxid_template.parse(mxid)

    @classmethod
    def get_mxid_from_id(cls, tgid: TelegramID) -> UserID:
        return UserID(cls.mxid_template.format_full(tgid))

    @classmethod
    def find_by_username(cls, username: str) -> Optional['Puppet']:
        if not username:
            return None

        username = username.lower()

        for _, puppet in cls.cache.items():
            if puppet.username and puppet.username.lower() == username:
                return puppet

        dbpuppet = DBPuppet.get_by_username(username)
        if dbpuppet:
            return cls.from_db(dbpuppet)

        return None

    @classmethod
    def find_by_displayname(cls, displayname: str) -> Optional['Puppet']:
        if not displayname:
            return None

        for _, puppet in cls.cache.items():
            if puppet.displayname and puppet.displayname == displayname:
                return puppet

        dbpuppet = DBPuppet.get_by_displayname(displayname)
        if dbpuppet:
            return cls.from_db(dbpuppet)

        return None
    # endregion


def init(context: 'Context') -> Iterable[Awaitable[Any]]:
    global config
    Puppet.az, config, Puppet.loop, _ = context.core
    Puppet.mx = context.mx
    Puppet.hs_domain = config["homeserver"]["domain"]

    Puppet.mxid_template = SimpleTemplate(config["bridge.username_template"], "userid",
                                          prefix="@", suffix=f":{Puppet.hs_domain}", type=int)
    Puppet.displayname_template = SimpleTemplate(config["bridge.displayname_template"],
                                                 "displayname")

    secret = config["bridge.login_shared_secret"]
    Puppet.login_shared_secret = secret.encode("utf-8") if secret else None
    Puppet.login_device_name = "Telegram Bridge"

    return (puppet.try_start() for puppet in Puppet.all_with_custom_mxid())
