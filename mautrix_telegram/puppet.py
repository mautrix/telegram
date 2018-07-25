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
from typing import Optional, Awaitable, Pattern, Dict, List, TYPE_CHECKING
from difflib import SequenceMatcher
import re
import logging
import asyncio

from sqlalchemy import orm

from telethon.tl.types import UserProfilePhoto
from mautrix_appservice import AppService, IntentAPI, IntentError, MatrixRequestError

from .db import Puppet as DBPuppet
from . import util

if TYPE_CHECKING:
    from .matrix import MatrixHandler
    from .config import Config
    from .context import Context

config = None  # type: Config


class Puppet:
    log = logging.getLogger("mau.puppet")  # type: logging.Logger
    db = None  # type: orm.Session
    az = None  # type: AppService
    mx = None  # type: MatrixHandler
    loop = None  # type: asyncio.AbstractEventLoop
    mxid_regex = None  # type: Pattern
    username_template = None  # type: str
    hs_domain = None  # type: str
    cache = {}  # type: Dict[str, Puppet]
    by_custom_mxid = {}  # type: Dict[str, Puppet]

    def __init__(self, id=None, access_token=None, custom_mxid=None, username=None,
                 displayname=None, displayname_source=None, photo_id=None, is_bot=None,
                 is_registered=False, db_instance=None):
        self.id = id
        self.access_token = access_token
        self.custom_mxid = custom_mxid
        self.is_real_user = self.custom_mxid and self.access_token
        self.default_mxid = self.get_mxid_from_id(self.id)
        self.mxid = self.custom_mxid or self.default_mxid

        self.username = username
        self.displayname = displayname
        self.displayname_source = displayname_source
        self.photo_id = photo_id
        self.is_bot = is_bot
        self.is_registered = is_registered
        self._db_instance = db_instance

        self.default_mxid_intent = self.az.intent.user(self.default_mxid)
        self.intent = None  # type: IntentAPI
        self.refresh_intents()

        self.cache[id] = self
        if self.custom_mxid:
            self.by_custom_mxid[self.custom_mxid] = self

    @property
    def tgid(self):
        return self.id

    @staticmethod
    async def is_logged_in():
        return True

    # region Custom puppet management
    def refresh_intents(self):
        self.is_real_user = self.custom_mxid and self.access_token
        self.intent = (self.az.intent.user(self.custom_mxid, self.access_token)
                       if self.is_real_user else self.default_mxid_intent)

    async def switch_mxid(self, access_token, mxid):
        prev_mxid = self.custom_mxid
        self.custom_mxid = mxid
        self.access_token = access_token
        self.refresh_intents()

        err = await self.init_custom_mxid()
        if err != 0:
            return err

        try:
            del self.by_custom_mxid[prev_mxid]
        except KeyError:
            pass
        self.mxid = self.custom_mxid or self.default_mxid
        if self.mxid != self.default_mxid:
            self.by_custom_mxid[self.mxid] = self
            await self.leave_rooms_with_default_user()
        self.save()
        return 0

    async def init_custom_mxid(self):
        if not self.is_real_user:
            return 0

        mxid = await self.intent.whoami()
        if not mxid or mxid != self.custom_mxid:
            self.custom_mxid = None
            self.access_token = None
            self.refresh_intents()
            if mxid != self.custom_mxid:
                return 2
            return 1
        if config["bridge.sync_with_custom_puppets"]:
            asyncio.ensure_future(self.sync(), loop=self.loop)
        return 0

    async def leave_rooms_with_default_user(self):
        for room_id in await self.default_mxid_intent.get_joined_rooms():
            try:
                await self.default_mxid_intent.leave_room(room_id)
                await self.intent.ensure_joined(room_id)
            except (IntentError, MatrixRequestError):
                pass

    def create_sync_filter(self) -> Awaitable[str]:
        return self.intent.client.create_filter(self.custom_mxid, {
            "room": {
                "include_leave": False,
                "state": {
                    "types": []
                },
                "timeline": {
                    "types": [],
                },
                "ephemeral": {
                    "types": ["m.typing", "m.receipt"],
                },
                "account_data": {
                    "types": []
                }
            },
            "account_data": {
                "types": [],
            },
            "presence": {
                "types": ["m.presence"],
                "senders": [self.custom_mxid],
            },
        })

    def filter_events(self, events):
        new_events = []
        for event in events:
            evt_type = event.get("type", None)
            event.setdefault("content", {})
            if evt_type == "m.typing":
                is_typing = self.custom_mxid in event["content"].get("user_ids", [])
                event["content"]["user_ids"] = [self.custom_mxid] if is_typing else []
            elif evt_type == "m.receipt":
                val = None
                evt = None
                for event_id in event["content"]:
                    try:
                        val = event["content"][event_id]["m.read"][self.custom_mxid]
                        evt = event_id
                        break
                    except KeyError:
                        pass
                if val and evt:
                    event["content"] = {evt: {"m.read": {
                        self.custom_mxid: val
                    }}}
                else:
                    continue
            new_events.append(event)
        return new_events

    def handle_sync(self, presence, ephemeral):
        presence = [self.mx.try_handle_event(event) for event in presence]

        for room_id, events in ephemeral.items():
            for event in events:
                event["room_id"] = room_id

        ephemeral = [self.mx.try_handle_event(event)
                     for events in ephemeral.values()
                     for event in self.filter_events(events)]

        events = ephemeral + presence
        coro = asyncio.gather(*events, loop=self.loop)
        asyncio.ensure_future(coro, loop=self.loop)

    async def sync(self):
        try:
            await self._sync()
        except Exception:
            self.log.exception("Fatal error syncing")

    async def _sync(self):
        if not self.is_real_user:
            self.log.warning("Called sync() for non-custom puppet.")
            return
        custom_mxid = self.custom_mxid
        access_token_at_start = self.access_token
        errors = 0
        next_batch = None
        filter_id = await self.create_sync_filter()
        self.log.debug(f"Starting syncer for {custom_mxid} with sync filter {filter_id}.")
        while access_token_at_start == self.access_token:
            try:
                sync_resp = await self.intent.client.sync(filter=filter_id, since=next_batch,
                                                          set_presence="offline")
                errors = 0
                if next_batch is not None:
                    presence = sync_resp.get("presence", {}).get("events", [])
                    ephemeral = {room: data.get("ephemeral", {}).get("events", [])
                                 for room, data
                                 in sync_resp.get("rooms", {}).get("join", {}).items()}
                    self.handle_sync(presence, ephemeral)
                next_batch = sync_resp.get("next_batch", None)
            except MatrixRequestError as e:
                wait = min(errors, 11) ** 2
                self.log.warning(f"Syncer for {custom_mxid} errored: {e}. "
                                 f"Waiting for {wait} seconds...")
                errors += 1
                await asyncio.sleep(wait)
        self.log.debug(f"Syncer for custom puppet {custom_mxid} stopped.")

    # endregion
    # region DB conversion

    @property
    def db_instance(self):
        if not self._db_instance:
            self._db_instance = self.new_db_instance()
        return self._db_instance

    def new_db_instance(self):
        return DBPuppet(id=self.id, access_token=self.access_token, custom_mxid=self.custom_mxid,
                        username=self.username, displayname=self.displayname,
                        displayname_source=self.displayname_source, photo_id=self.photo_id,
                        is_bot=self.is_bot, matrix_registered=self.is_registered)

    @classmethod
    def from_db(cls, db_puppet):
        return Puppet(db_puppet.id, db_puppet.access_token, db_puppet.custom_mxid,
                      db_puppet.username, db_puppet.displayname, db_puppet.displayname_source,
                      db_puppet.photo_id, db_puppet.is_bot, db_puppet.matrix_registered,
                      db_instance=db_puppet)

    def save(self):
        self.db_instance.access_token = self.access_token
        self.db_instance.custom_mxid = self.custom_mxid
        self.db_instance.username = self.username
        self.db_instance.displayname = self.displayname
        self.db_instance.displayname_source = self.displayname_source
        self.db_instance.photo_id = self.photo_id
        self.db_instance.is_bot = self.is_bot
        self.db_instance.matrix_registered = self.is_registered
        self.db.commit()

    # endregion
    # region Info updating
    def similarity(self, query):
        username_similarity = (SequenceMatcher(None, self.username, query).ratio()
                               if self.username else 0)
        displayname_similarity = (SequenceMatcher(None, self.displayname, query).ratio()
                                  if self.displayname else 0)
        similarity = max(username_similarity, displayname_similarity)
        return round(similarity * 1000) / 10

    @staticmethod
    def get_displayname(info, enable_format=True):
        data = {
            "phone number": info.phone if hasattr(info, "phone") else None,
            "username": info.username,
            "full name": " ".join([info.first_name or "", info.last_name or ""]).strip(),
            "full name reversed": " ".join([info.first_name or "", info.last_name or ""]).strip(),
            "first name": info.first_name,
            "last name": info.last_name,
        }
        preferences = config.get("bridge.displayname_preference",
                                 ["full name", "username", "phone"])
        name = None
        for preference in preferences:
            name = data[preference]
            if name:
                break

        if info.deleted:
            name = f"Deleted account {info.id}"
        elif not name:
            name = info.id

        if not enable_format:
            return name
        return config.get("bridge.displayname_template", "{displayname} (Telegram)").format(
            displayname=name)

    async def update_info(self, source, info):
        changed = False
        if self.username != info.username:
            self.username = info.username
            changed = True

        changed = await self.update_displayname(source, info) or changed
        if isinstance(info.photo, UserProfilePhoto):
            changed = await self.update_avatar(source, info.photo.photo_big) or changed

        self.is_bot = info.bot

        if changed:
            self.save()

    async def update_displayname(self, source, info):
        ignore_source = (not source.is_relaybot
                         and self.displayname_source is not None
                         and self.displayname_source != source.tgid)
        if ignore_source:
            return

        displayname = self.get_displayname(info)
        if displayname != self.displayname:
            await self.default_mxid_intent.set_display_name(displayname)
            self.displayname = displayname
            self.displayname_source = source.tgid
            return True
        elif source.is_relaybot or self.displayname_source is None:
            self.displayname_source = source.tgid
            return True

    async def update_avatar(self, source, photo):
        photo_id = f"{photo.volume_id}-{photo.local_id}"
        if self.photo_id != photo_id:
            file = await util.transfer_file_to_matrix(self.db, source.client,
                                                      self.default_mxid_intent, photo)
            if file:
                await self.default_mxid_intent.set_avatar(file.mxc)
                self.photo_id = photo_id
                return True
        return False

    # endregion
    # region Getters

    @classmethod
    def get(cls, tgid, create=True) -> "Optional[Puppet]":
        try:
            return cls.cache[tgid]
        except KeyError:
            pass

        puppet = DBPuppet.query.get(tgid)
        if puppet:
            return cls.from_db(puppet)

        if create:
            puppet = cls(tgid)
            cls.db.add(puppet.db_instance)
            cls.db.commit()
            return puppet

        return None

    @classmethod
    def get_by_mxid(cls, mxid, create=True) -> "Optional[Puppet]":
        tgid = cls.get_id_from_mxid(mxid)
        return cls.get(tgid, create) if tgid else None

    @classmethod
    def get_by_custom_mxid(cls, mxid):
        if not mxid:
            raise ValueError("Matrix ID can't be empty")

        try:
            return cls.by_custom_mxid[mxid]
        except KeyError:
            pass

        puppet = DBPuppet.query.filter(DBPuppet.custom_mxid == mxid).one_or_none()
        if puppet:
            puppet = cls.from_db(puppet)
            return puppet

        return None

    @classmethod
    def get_all_with_custom_mxid(cls):
        return [cls.by_custom_mxid[puppet.mxid]
                if puppet.custom_mxid in cls.by_custom_mxid
                else cls.from_db(puppet)
                for puppet in DBPuppet.query.filter(DBPuppet.custom_mxid is not None).all()]

    @classmethod
    def get_id_from_mxid(cls, mxid):
        match = cls.mxid_regex.match(mxid)
        if match:
            return int(match.group(1))
        return None

    @classmethod
    def get_mxid_from_id(cls, tgid):
        return f"@{cls.username_template.format(userid=tgid)}:{cls.hs_domain}"

    @classmethod
    def find_by_username(cls, username) -> "Optional[Puppet]":
        if not username:
            return None

        for _, puppet in cls.cache.items():
            if puppet.username and puppet.username.lower() == username.lower():
                return puppet

        puppet = DBPuppet.query.filter(DBPuppet.username == username).one_or_none()
        if puppet:
            return cls.from_db(puppet)

        return None

    @classmethod
    def find_by_displayname(cls, displayname) -> "Optional[Puppet]":
        if not displayname:
            return None

        for _, puppet in cls.cache.items():
            if puppet.displayname and puppet.displayname == displayname:
                return puppet

        puppet = DBPuppet.query.filter(DBPuppet.displayname == displayname).one_or_none()
        if puppet:
            return cls.from_db(puppet)

        return None
    # endregion


def init(context: "Context") -> List[Awaitable[int]]:
    global config
    Puppet.az, Puppet.db, config, Puppet.loop, _ = context
    Puppet.mx = context.mx
    Puppet.username_template = config.get("bridge.username_template", "telegram_{userid}")
    Puppet.hs_domain = config["homeserver"]["domain"]
    Puppet.mxid_regex = re.compile(
        f"@{Puppet.username_template.format(userid='(.+)')}:{Puppet.hs_domain}")
    return [puppet.init_custom_mxid() for puppet in Puppet.get_all_with_custom_mxid()]
