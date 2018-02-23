# -*- coding: future_fstrings -*-
# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2018 Tulir Asokan
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
from difflib import SequenceMatcher
import re
import logging

from telethon.tl.types import UserProfilePhoto
from telethon.errors.rpc_error_list import LocationInvalidError

from .db import Puppet as DBPuppet
from . import util

config = None


class Puppet:
    log = logging.getLogger("mau.puppet")
    db = None
    az = None
    mxid_regex = None
    username_template = None
    hs_domain = None
    cache = {}

    def __init__(self, id=None, username=None, displayname=None, photo_id=None, db_instance=None):
        self.id = id
        self.mxid = self.get_mxid_from_id(self.id)

        self.username = username
        self.displayname = displayname
        self.photo_id = photo_id
        self._db_instance = db_instance

        self.intent = self.az.intent.user(self.mxid)

        self.cache[id] = self

    @property
    def tgid(self):
        return self.id

    @property
    def db_instance(self):
        if not self._db_instance:
            self._db_instance = self.new_db_instance()
        return self._db_instance

    def new_db_instance(self):
        return DBPuppet(id=self.id, username=self.username, displayname=self.displayname,
                        photo_id=self.photo_id)

    @classmethod
    def from_db(cls, db_puppet):
        return Puppet(db_puppet.id, db_puppet.username, db_puppet.displayname, db_puppet.photo_id,
                      db_instance=db_puppet)

    def save(self):
        self.db_instance.username = self.username
        self.db_instance.displayname = self.displayname
        self.db_instance.photo_id = self.photo_id
        self.db.commit()

    def similarity(self, query):
        username_similarity = (SequenceMatcher(None, self.username, query).ratio()
                               if self.username else 0)
        displayname_similarity = (SequenceMatcher(None, self.displayname, query).ratio()
                                  if self.displayname else 0)
        similarity = max(username_similarity, displayname_similarity)
        return round(similarity * 1000) / 10

    @staticmethod
    def get_displayname(info, format=True):
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
        if not name:
            name = info.id

        if not format:
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

        if changed:
            self.save()

    async def update_displayname(self, source, info):
        displayname = self.get_displayname(info)
        if displayname != self.displayname:
            await self.intent.set_display_name(displayname)
            self.displayname = displayname
            return True

    async def update_avatar(self, source, photo):
        photo_id = f"{photo.volume_id}-{photo.local_id}"
        if self.photo_id != photo_id:
            file = await util.transfer_file_to_matrix(self.db, source.client, self.intent, photo)
            if file:
                await self.intent.set_avatar(file.mxc)
                self.photo_id = photo_id
                return True
        return False

    @classmethod
    def get(cls, id, create=True):
        try:
            return cls.cache[id]
        except KeyError:
            pass

        puppet = DBPuppet.query.get(id)
        if puppet:
            return cls.from_db(puppet)

        if create:
            puppet = cls(id)
            cls.db.add(puppet.db_instance)
            cls.db.commit()
            return puppet

        return None

    @classmethod
    def get_by_mxid(cls, mxid, create=True):
        tgid = cls.get_id_from_mxid(mxid)
        return cls.get(tgid, create) if tgid else None

    @classmethod
    def get_id_from_mxid(cls, mxid):
        match = cls.mxid_regex.match(mxid)
        if match:
            return int(match.group(1))
        return None

    @classmethod
    def get_mxid_from_id(cls, id):
        return f"@{cls.username_template.format(userid=id)}:{cls.hs_domain}"

    @classmethod
    def find_by_username(cls, username):
        if not username:
            return None

        for _, puppet in cls.cache.items():
            if puppet.username and puppet.username.lower() == username.lower():
                return puppet

        puppet = DBPuppet.query.filter(DBPuppet.username == username).one_or_none()
        if puppet:
            return cls.from_db(puppet)

        return None


def init(context):
    global config
    Puppet.az, Puppet.db, config, _, _ = context
    Puppet.username_template = config.get("bridge.username_template", "telegram_{userid}")
    Puppet.hs_domain = config["homeserver"]["domain"]
    localpart = Puppet.username_template.format(userid="(.+)")
    Puppet.mxid_regex = re.compile(f"@{localpart}:{Puppet.hs_domain}")
