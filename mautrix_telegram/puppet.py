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
import re
from telethon.tl.types import UserProfilePhoto
from .db import Puppet as DBPuppet

config = None


class Puppet:
    log = None
    db = None
    az = None
    cache = {}

    def __init__(self, id=None, username=None, displayname=None, photo_id=None):
        self.id = id

        self.localpart = config.get("bridge.username_template", "telegram_{userid}").format(userid=self.id)
        hs = config["homeserver"]["domain"]
        self.mxid = f"@{self.localpart}:{hs}"
        self.username = username
        self.displayname = displayname
        self.photo_id = photo_id
        self.intent = self.az.intent.user(self.mxid)

        self.cache[id] = self

    @property
    def tgid(self):
        return self.id

    def to_db(self):
        return self.db.merge(
            DBPuppet(id=self.id, username=self.username, displayname=self.displayname,
                     photo_id=self.photo_id))

    @classmethod
    def from_db(cls, db_puppet):
        return Puppet(db_puppet.id, db_puppet.username, db_puppet.displayname, db_puppet.photo_id)

    def save(self):
        self.to_db()
        self.db.commit()

    @staticmethod
    def get_displayname(info, format=True):
        data = {
            "phone_number": info.phone,
            "username": info.username,
            "full name": " ".join([info.first_name or "", info.last_name or ""]).strip(),
            "full name reversed": " ".join([info.first_name or "", info.last_name or ""]).strip(),
            "first name": info.first_name,
            "last_name": info.last_name,
        }
        preferences = config.get("bridge", {}).get("displayname_preference",
                                                   ["full name", "username", "phone"])
        for preference in preferences:
            name = data[preference]
            if name:
                break

        if not format:
            return name
        return config.get("bridge.displayname_template", "{displayname} (Telegram)").format(displayname=name)

    def update_info(self, source, info):
        changed = False
        if self.username != info.username:
            self.username = info.username
            changed = True

        displayname = self.get_displayname(info)
        if displayname != self.displayname:
            self.intent.set_display_name(displayname)
            self.displayname = displayname
            changed = True

        if isinstance(info.photo, UserProfilePhoto):
            changed = self.update_avatar(source, info.photo.photo_big)

        if changed:
            self.save()

    def update_avatar(self, source, photo):
        photo_id = f"{photo.volume_id}-{photo.local_id}"
        if self.photo_id != photo_id:
            file = source.download_file(photo)
            uploaded = self.intent.upload_file(file)
            self.intent.set_avatar(uploaded["content_uri"])
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
            cls.db.add(puppet.to_db())
            cls.db.commit()
            return puppet

        return None

    @classmethod
    def find_by_username(cls, username):
        for _, puppet in cls.cache.items():
            if puppet.username == username:
                return puppet

        puppet = DBPuppet.query.filter(DBPuppet.username == username).one_or_none()
        if puppet:
            return cls.from_db(puppet)

        return None


def init(context):
    global config
    Puppet.az, Puppet.db, log, config = context
    Puppet.log = log.getChild("puppet")
    localpart = config.get("bridge.username_template", "telegram_{userid}").format(userid="(.+)")
    hs = config["homeserver"]["domain"]
    Puppet.mxid_regex = re.compile(f"@{localpart}:{hs}")
