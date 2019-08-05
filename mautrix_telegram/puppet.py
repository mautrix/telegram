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
import asyncio
import logging

from telethon.tl.types import (UserProfilePhoto, User, UpdateUserName, PeerUser, TypeInputPeer,
                               InputPeerPhotoFileLocation, UserProfilePhotoEmpty, TypeInputUser)

from mautrix.appservice import AppService, IntentAPI
from mautrix.errors import MatrixRequestError
from mautrix.bridge import CustomPuppetMixin
from mautrix.types import UserID

from .types import TelegramID
from .db import Puppet as DBPuppet
from . import util

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
    username_template: str
    hs_domain: str
    _mxid_prefix: str
    _mxid_suffix: str
    _displayname_prefix: str
    _displayname_suffix: str

    cache: Dict[TelegramID, 'Puppet'] = {}
    by_custom_mxid: Dict[UserID, 'Puppet'] = {}

    id: TelegramID
    access_token: Optional[str]
    custom_mxid: Optional[UserID]
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

    @property
    def tgid(self) -> TelegramID:
        return self.id

    @property
    def peer(self) -> PeerUser:
        return PeerUser(user_id=self.tgid)

    @staticmethod
    async def is_logged_in() -> bool:
        """ Is True if the puppet is logged in. """
        return True

    @property
    def plain_displayname(self) -> str:
        prefix = self._mxid_prefix
        suffix = self._mxid_suffix
        if self.displayname[:len(prefix)] == prefix and self.displayname[-len(suffix):] == suffix:
            return self.displayname[len(prefix):-len(suffix)]
        return self.displayname

    def get_input_entity(self, user: 'AbstractUser'
                         ) -> Awaitable[Union[TypeInputPeer, TypeInputUser]]:
        return user.client.get_input_entity(self.peer)

    # region DB conversion

    @property
    def db_instance(self) -> DBPuppet:
        if not self._db_instance:
            self._db_instance = self.new_db_instance()
        return self._db_instance

    def new_db_instance(self) -> DBPuppet:
        return DBPuppet(id=self.id, access_token=self.access_token, custom_mxid=self.custom_mxid,
                        username=self.username, displayname=self.displayname,
                        displayname_source=self.displayname_source, photo_id=self.photo_id,
                        is_bot=self.is_bot, matrix_registered=self.is_registered,
                        disable_updates=self.disable_updates)

    @classmethod
    def from_db(cls, db_puppet: DBPuppet) -> 'Puppet':
        return Puppet(db_puppet.id, db_puppet.access_token, db_puppet.custom_mxid,
                      db_puppet.username, db_puppet.displayname, db_puppet.displayname_source,
                      db_puppet.photo_id, db_puppet.is_bot, db_puppet.matrix_registered,
                      db_puppet.disable_updates, db_instance=db_puppet)

    def save(self) -> None:
        self.db_instance.edit(access_token=self.access_token, custom_mxid=self.custom_mxid,
                              username=self.username, displayname=self.displayname,
                              displayname_source=self.displayname_source, photo_id=self.photo_id,
                              is_bot=self.is_bot, matrix_registered=self.is_registered,
                              disable_updates=self.disable_updates)

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
        whitespace = ("\ufeff", "\u3164", "\u2063", "\u200b", "\u180e", "\u034f", "\u2800",
                      "\u180e", "\u200b", "\u202f", "\u205f", "\u3000")
        name = "".join(char for char in name if char not in whitespace)
        name = name.strip()
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
        return config["bridge.displayname_template"].format(
            displayname=name)

    async def update_info(self, source: 'AbstractUser', info: User) -> None:
        if self.disable_updates:
            return
        changed = False
        if self.username != info.username:
            self.username = info.username
            changed = True

        changed = await self.update_displayname(source, info) or changed
        if isinstance(info.photo, UserProfilePhoto):
            changed = await self.update_avatar(source, info.photo) or changed

        self.is_bot = info.bot

        if changed:
            self.save()

    async def update_displayname(self, source: 'AbstractUser', info: Union[User, UpdateUserName]
                                 ) -> bool:
        if self.disable_updates:
            return False
        allow_source = (source.is_relaybot
                        or self.displayname_source == source.tgid
                        # No displayname source, so just trust anything
                        or self.displayname_source is None
                        # No phone -> not in contact list -> can't set custom name
                        or (isinstance(info, User) and info.phone is None))
        if not allow_source:
            return False
        elif isinstance(info, UpdateUserName):
            info = await source.client.get_entity(PeerUser(self.tgid))

        displayname = self.get_displayname(info)
        if displayname != self.displayname:
            self.displayname = displayname
            self.displayname_source = source.tgid
            try:
                await self.default_mxid_intent.set_displayname(displayname[:100])
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
        prefix = cls._mxid_prefix
        suffix = cls._mxid_suffix
        if mxid[:len(prefix)] == prefix and mxid[-len(suffix):] == suffix:
            return TelegramID(int(mxid[len(prefix):-len(suffix)]))
        return None

    @classmethod
    def get_mxid_from_id(cls, tgid: TelegramID) -> UserID:
        return UserID(f"@{cls.username_template.format(userid=tgid)}:{cls.hs_domain}")

    @classmethod
    def find_by_username(cls, username: str) -> Optional['Puppet']:
        if not username:
            return None

        for _, puppet in cls.cache.items():
            if puppet.username and puppet.username.lower() == username.lower():
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

    Puppet.username_template = config["bridge.username_template"]
    index = Puppet.username_template.index("{userid}")
    length = len("{userid}")
    Puppet._mxid_prefix = f"@{Puppet.username_template[:index]}"
    Puppet._mxid_suffix = f"{Puppet.username_template[index + length:]}:{Puppet.hs_domain}"

    displayname_template = config["bridge.displayname_template"]
    index = displayname_template.index("{displayname}")
    length = len("{displayname}")
    Puppet._displayname_prefix = displayname_template[:index]
    Puppet._displayname_suffix = displayname_template[index + length:]

    return (puppet.start() for puppet in Puppet.all_with_custom_mxid())
