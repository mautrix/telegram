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
from telethon import TelegramClient
from telethon.tl.types import User as UserEntity, Chat as ChatEntity, Channel as ChannelEntity
from .db import Puppet as DBPuppet
from . import portal as p

config = None


class Puppet:
    cache = {}

    def __init__(self, id=None, username=None, displayname=None):
        self.id = id

        self.localpart = config.get("bridge.alias_template", "telegram_{}").format(self.id)
        hs = config["homeserver"]["domain"]
        self.mxid = f"@{self.localpart}:{hs}"
        self.username = username
        self.displayname = displayname
        self.intent = self.az.intent.user(self.mxid)

        self.cache[id] = self

    def to_db(self):
        return self.db.merge(
            DBPuppet(id=self.id, username=self.username, displayname=self.displayname))

    @classmethod
    def from_db(cls, db_puppet):
        return Puppet(db_puppet.id, db_puppet.username, db_puppet.displayname)

    def save(self):
        self.to_db()
        self.db.commit()

    def get_displayname(self, info):
        if info.first_name or info.last_name:
            name = " ".join([info.first_name or "", info.last_name or ""]).strip()
        elif info.username:
            name = info.username
        elif info.phone_number:
            name = info.phone_number
        else:
            name = info.id
        return config.get("bridge.displayname_template", "{} (Telegram)").format(name)

    def update_info(self, info):
        changed = False
        if self.username != info.username:
            self.username = info.username
            changed = True
        displayname = self.get_displayname(info)
        if displayname != self.displayname:
            self.intent.set_display_name(displayname)
            self.displayname = displayname
            changed = True

        if changed:
            self.save()

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
