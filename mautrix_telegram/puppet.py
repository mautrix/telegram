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

from typing import TYPE_CHECKING, AsyncGenerator, AsyncIterable, Awaitable, cast
from difflib import SequenceMatcher
import unicodedata

from telethon.tl.types import (
    Channel,
    ChatPhoto,
    ChatPhotoEmpty,
    InputPeerPhotoFileLocation,
    InputPeerUser,
    PeerChannel,
    PeerChat,
    PeerUser,
    TypeChatPhoto,
    TypeInputPeer,
    TypeInputUser,
    TypePeer,
    TypeUserProfilePhoto,
    UpdateUserName,
    User,
    UserProfilePhoto,
    UserProfilePhotoEmpty,
)
from yarl import URL

from mautrix.appservice import IntentAPI
from mautrix.bridge import BasePuppet, async_getter_lock
from mautrix.types import ContentURI, RoomID, SyncToken, UserID
from mautrix.util.simple_template import SimpleTemplate

from . import abstract_user as au, portal as p, util
from .config import Config
from .db import Puppet as DBPuppet
from .types import TelegramID

if TYPE_CHECKING:
    from .__main__ import TelegramBridge


class Puppet(DBPuppet, BasePuppet):
    config: Config
    hs_domain: str
    mxid_template: SimpleTemplate[TelegramID]
    displayname_template: SimpleTemplate[str]

    by_tgid: dict[TelegramID, Puppet] = {}
    by_custom_mxid: dict[UserID, Puppet] = {}

    def __init__(
        self,
        id: TelegramID,
        is_registered: bool = False,
        displayname: str | None = None,
        displayname_source: TelegramID | None = None,
        displayname_contact: bool = True,
        displayname_quality: int = 0,
        disable_updates: bool = False,
        username: str | None = None,
        phone: str | None = None,
        photo_id: str | None = None,
        avatar_url: ContentURI | None = None,
        name_set: bool = False,
        avatar_set: bool = False,
        is_bot: bool = False,
        is_channel: bool = False,
        is_premium: bool = False,
        custom_mxid: UserID | None = None,
        access_token: str | None = None,
        next_batch: SyncToken | None = None,
        base_url: str | None = None,
    ) -> None:
        super().__init__(
            id=id,
            is_registered=is_registered,
            displayname=displayname,
            displayname_source=displayname_source,
            displayname_contact=displayname_contact,
            displayname_quality=displayname_quality,
            disable_updates=disable_updates,
            username=username,
            phone=phone,
            photo_id=photo_id,
            avatar_url=avatar_url,
            name_set=name_set,
            avatar_set=avatar_set,
            is_bot=is_bot,
            is_channel=is_channel,
            is_premium=is_premium,
            custom_mxid=custom_mxid,
            access_token=access_token,
            next_batch=next_batch,
            base_url=base_url,
        )

        self.default_mxid = self.get_mxid_from_id(self.id)
        self.default_mxid_intent = self.az.intent.user(self.default_mxid)
        self.intent = self._fresh_intent()

        self.by_tgid[id] = self
        if self.custom_mxid:
            self.by_custom_mxid[self.custom_mxid] = self

        self.log = self.log.getChild(str(self.id))

    @property
    def tgid(self) -> TelegramID:
        return self.id

    @property
    def tg_username(self) -> str | None:
        return self.username

    @property
    def peer(self) -> PeerUser:
        return (
            PeerChannel(channel_id=self.tgid) if self.is_channel else PeerUser(user_id=self.tgid)
        )

    @property
    def contact_info(self) -> dict:
        return {
            "name": self.displayname,
            "username": self.username,
            "phone": f"+{self.phone.lstrip('+')}" if self.phone else None,
            "is_bot": self.is_bot,
            "avatar_url": self.avatar_url,
        }

    @property
    def plain_displayname(self) -> str:
        return self.displayname_template.parse(self.displayname) or self.displayname

    def get_input_entity(self, user: au.AbstractUser) -> Awaitable[TypeInputPeer | TypeInputUser]:
        return user.client.get_input_entity(self.peer)

    def intent_for(self, portal: p.Portal) -> IntentAPI:
        if portal.tgid == self.tgid:
            return self.default_mxid_intent
        return self.intent

    @classmethod
    def init_cls(cls, bridge: "TelegramBridge") -> AsyncIterable[Awaitable[None]]:
        cls.config = bridge.config
        cls.loop = bridge.loop
        cls.mx = bridge.matrix
        cls.az = bridge.az
        cls.hs_domain = cls.config["homeserver.domain"]
        mxid_tpl = SimpleTemplate(
            cls.config["bridge.username_template"],
            "userid",
            prefix="@",
            suffix=f":{Puppet.hs_domain}",
            type=int,
        )
        cls.mxid_template = cast(SimpleTemplate[TelegramID], mxid_tpl)
        cls.displayname_template = SimpleTemplate(
            cls.config["bridge.displayname_template"], "displayname"
        )
        cls.sync_with_custom_puppets = cls.config["bridge.sync_with_custom_puppets"]
        cls.homeserver_url_map = {
            server: URL(url)
            for server, url in cls.config["bridge.double_puppet_server_map"].items()
        }
        cls.allow_discover_url = cls.config["bridge.double_puppet_allow_discovery"]
        cls.login_shared_secret_map = {
            server: secret.encode("utf-8")
            for server, secret in cls.config["bridge.login_shared_secret_map"].items()
        }
        cls.login_device_name = "Telegram Bridge"

        return (puppet.try_start() async for puppet in cls.all_with_custom_mxid())

    # region Info updating

    def similarity(self, query: str) -> int:
        username_similarity = (
            SequenceMatcher(None, self.username, query).ratio() if self.username else 0
        )
        displayname_similarity = (
            SequenceMatcher(None, self.plain_displayname, query).ratio() if self.displayname else 0
        )
        similarity = max(username_similarity, displayname_similarity)
        return int(round(similarity * 100))

    @staticmethod
    def _filter_name(name: str) -> str:
        if not name:
            return ""
        whitespace = (
            "\t\n\r\v\f \u00a0\u034f\u180e\u2063\u202f\u205f\u2800\u3000\u3164\ufeff\u2000\u2001"
            "\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u200b\u200c\u200d\u200e\u200f"
            "\ufe0f"
        )
        allowed_other_format = ("\u200d", "\u200c")
        name = "".join(
            c
            for c in name.strip(whitespace)
            if unicodedata.category(c) != "Cf" or c in allowed_other_format
        )
        return name

    @classmethod
    def get_displayname(cls, info: User | Channel, enable_format: bool = True) -> tuple[str, int]:
        if isinstance(info, Channel):
            fn, ln = cls._filter_name(info.title), ""
        else:
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
        preferences = cls.config["bridge.displayname_preference"]
        name = None
        quality = 99
        for preference in preferences:
            name = data[preference]
            if name:
                break
            quality -= 1

        if isinstance(info, User) and info.deleted:
            name = f"Deleted account {info.id}"
            quality = 99
        elif not name:
            name = str(info.id)
            quality = 0

        return (cls.displayname_template.format_full(name) if enable_format else name), quality

    async def try_update_info(self, source: au.AbstractUser, info: User | Channel) -> None:
        try:
            await self.update_info(source, info)
        except Exception:
            source.log.exception(f"Failed to update info of {self.tgid}")

    async def update_info(self, source: au.AbstractUser, info: User | Channel) -> None:
        is_bot = False if isinstance(info, Channel) else info.bot
        is_premium = False if isinstance(info, Channel) else info.premium
        is_channel = isinstance(info, Channel)
        changed = (
            is_bot != self.is_bot or is_channel != self.is_channel or is_premium != self.is_premium
        )

        self.is_bot = is_bot
        self.is_channel = is_channel
        self.is_premium = is_premium

        if self.username != info.username:
            self.username = info.username
            changed = True

        if getattr(info, "phone", None) and self.phone != info.phone:
            self.phone = info.phone
            changed = True

        if not self.disable_updates:
            try:
                changed = await self.update_displayname(source, info) or changed
                changed = await self.update_avatar(source, info.photo) or changed
            except Exception:
                self.log.exception(f"Failed to update info from source {source.tgid}")

        if changed:
            await self.update_portals_meta()
            await self.save()

    async def update_portals_meta(self) -> None:
        if not p.Portal.private_chat_portal_meta and not self.mx.e2ee:
            return
        async for portal in p.Portal.find_private_chats_with(self.tgid):
            await portal.update_info_from_puppet(self)

    async def update_displayname(
        self, source: au.AbstractUser, info: User | Channel | UpdateUserName
    ) -> bool:
        if self.disable_updates:
            return False
        if (
            self.displayname
            and self.displayname.startswith("Deleted user ")
            and not getattr(info, "deleted", False)
        ):
            allow_because = "target user was previously deleted"
            self.displayname_quality = 0
        elif source.is_relaybot or source.is_bot:
            allow_because = "source user is a bot"
        elif self.displayname_source == source.tgid:
            allow_because = "source user is the primary source"
        elif isinstance(info, Channel):
            allow_because = "target user is a channel"
        elif not isinstance(info, UpdateUserName) and not info.contact:
            allow_because = "target user is not a contact"
        elif not self.displayname_source:
            allow_because = "no primary source set"
        elif not self.displayname:
            allow_because = "target user has no name"
        else:
            return False

        if isinstance(info, UpdateUserName):
            info = await source.client.get_entity(self.peer)
        if isinstance(info, Channel) or not info.contact:
            self.displayname_contact = False
        elif not self.displayname_contact:
            if not self.displayname:
                self.displayname_contact = True
            else:
                return False

        displayname, quality = self.get_displayname(info)
        needs_reset = displayname != self.displayname or not self.name_set
        is_high_quality = quality >= self.displayname_quality
        if needs_reset and is_high_quality:
            allow_because = f"{allow_because} and quality {quality} >= {self.displayname_quality}"
            self.log.debug(
                f"Updating displayname of {self.id} (src: {source.tgid}, allowed "
                f"because {allow_because}) from {self.displayname} to {displayname}"
            )
            self.log.trace("Displayname source data: %s", info)
            self.displayname = displayname
            self.displayname_source = source.tgid
            self.displayname_quality = quality
            try:
                await self.default_mxid_intent.set_displayname(
                    displayname[: self.config["bridge.displayname_max_length"]]
                )
                self.name_set = True
            except Exception as e:
                self.log.warning(f"Failed to set displayname: {e}")
                self.name_set = False
            return True
        elif source.is_relaybot or self.displayname_source is None:
            self.displayname_source = source.tgid
            return True
        return False

    async def update_avatar(
        self, source: au.AbstractUser, photo: TypeUserProfilePhoto | TypeChatPhoto
    ) -> bool:
        if self.disable_updates:
            return False

        if photo is None or isinstance(photo, (UserProfilePhotoEmpty, ChatPhotoEmpty)):
            photo_id = ""
        elif isinstance(photo, (UserProfilePhoto, ChatPhoto)):
            photo_id = str(photo.photo_id)
        else:
            self.log.warning(f"Unknown user profile photo type: {type(photo)}")
            return False
        if not photo_id and not self.config["bridge.allow_avatar_remove"]:
            return False
        if self.photo_id != photo_id or not self.avatar_set:
            if not photo_id:
                self.photo_id = ""
                self.avatar_url = None
            elif self.photo_id != photo_id or not self.avatar_url:
                file = await util.transfer_file_to_matrix(
                    client=source.client,
                    intent=self.default_mxid_intent,
                    location=InputPeerPhotoFileLocation(
                        peer=await self.get_input_entity(source), photo_id=photo.photo_id, big=True
                    ),
                    async_upload=self.config["homeserver.async_media"],
                )
                if not file:
                    return False
                self.photo_id = photo_id
                self.avatar_url = file.mxc
            try:
                await self.default_mxid_intent.set_avatar_url(self.avatar_url or "")
                self.avatar_set = True
            except Exception as e:
                self.log.warning(f"Failed to set avatar: {e}")
                self.avatar_set = False
            return True
        return False

    async def default_puppet_should_leave_room(self, room_id: RoomID) -> bool:
        portal: p.Portal = await p.Portal.get_by_mxid(room_id)
        return portal and not portal.backfill_lock.locked and portal.peer_type != "user"

    # endregion
    # region Getters

    def _add_to_cache(self) -> None:
        self.by_tgid[self.id] = self
        if self.custom_mxid:
            self.by_custom_mxid[self.custom_mxid] = self

    @classmethod
    @async_getter_lock
    async def get_by_tgid(
        cls, tgid: TelegramID, /, *, create: bool = True, is_channel: bool = False
    ) -> Puppet | None:
        if tgid is None:
            return None

        try:
            return cls.by_tgid[tgid]
        except KeyError:
            pass

        puppet = cast(cls, await super().get_by_tgid(tgid))
        if puppet:
            puppet._add_to_cache()
            return puppet

        if create:
            puppet = cls(tgid, is_channel=is_channel)
            await puppet.insert()
            puppet._add_to_cache()
            return puppet

        return None

    @staticmethod
    def get_id_from_peer(peer: TypePeer | User | Channel) -> TelegramID:
        if isinstance(peer, (PeerUser, InputPeerUser)):
            return TelegramID(peer.user_id)
        elif isinstance(peer, PeerChannel):
            return TelegramID(peer.channel_id)
        elif isinstance(peer, PeerChat):
            return TelegramID(peer.chat_id)
        elif isinstance(peer, (User, Channel)):
            return TelegramID(peer.id)
        raise TypeError(f"invalid type {type(peer).__name__!r} in _id_from_peer()")

    @classmethod
    async def get_by_peer(
        cls, peer: TypePeer | User | Channel, *, create: bool = True
    ) -> Puppet | None:
        if isinstance(peer, PeerChat):
            return None
        return await cls.get_by_tgid(
            cls.get_id_from_peer(peer),
            create=create,
            is_channel=isinstance(peer, (PeerChannel, Channel)),
        )

    @classmethod
    def get_by_mxid(cls, mxid: UserID, create: bool = True) -> Awaitable[Puppet | None]:
        return cls.get_by_tgid(cls.get_id_from_mxid(mxid), create=create)

    @classmethod
    @async_getter_lock
    async def get_by_custom_mxid(cls, mxid: UserID, /) -> Puppet | None:
        try:
            return cls.by_custom_mxid[mxid]
        except KeyError:
            pass

        puppet = cast(cls, await super().get_by_custom_mxid(mxid))
        if puppet:
            puppet._add_to_cache()
            return puppet

        return None

    @classmethod
    async def all_with_custom_mxid(cls) -> AsyncGenerator[Puppet, None]:
        puppets = await super().all_with_custom_mxid()
        puppet: cls
        for puppet in puppets:
            try:
                yield cls.by_tgid[puppet.tgid]
            except KeyError:
                puppet._add_to_cache()
                yield puppet

    @classmethod
    def get_id_from_mxid(cls, mxid: UserID) -> TelegramID | None:
        return cls.mxid_template.parse(mxid)

    @classmethod
    def get_mxid_from_id(cls, tgid: TelegramID) -> UserID:
        return UserID(cls.mxid_template.format_full(tgid))

    @classmethod
    async def find_by_username(cls, username: str) -> Puppet | None:
        if not username:
            return None

        username = username.lower()

        for _, puppet in cls.by_tgid.items():
            if puppet.username and puppet.username.lower() == username:
                return puppet

        puppet = cast(cls, await super().find_by_username(username))
        if puppet:
            try:
                return cls.by_tgid[puppet.tgid]
            except KeyError:
                puppet._add_to_cache()
                return puppet

        return None

    # endregion
