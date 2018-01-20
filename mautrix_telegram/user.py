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
import traceback
from telethon import TelegramClient
from telethon.tl.types import User as UserEntity, Chat as ChatEntity, Channel as ChannelEntity, \
    UpdateShortMessage, UpdateShortChatMessage
from .db import User as DBUser
from . import portal as po, puppet as pu

config = None


class User:
    by_mxid = {}
    by_tgid = {}

    def __init__(self, mxid, tgid=None):
        self.mxid = mxid
        self.tgid = tgid

        self.command_status = None
        self.connected = False
        self.logged_in = False
        self.client = None

        self.by_mxid[mxid] = self
        if tgid:
            self.by_tgid[tgid] = self

    def to_db(self):
        return self.db.merge(DBUser(self.mxid, self.tgid))

    def save(self):
        self.to_db()
        self.db.commit()

    @classmethod
    def from_db(cls, db_user):
        return User(db_user.mxid, db_user.tgid)

    def start(self):
        self.client = TelegramClient(self.mxid,
                                     config["telegram.api_id"],
                                     config["telegram.api_hash"],
                                     update_workers=2)
        self.connected = self.client.connect()
        self.logged_in = self.client.is_user_authorized()
        if self.logged_in:
            self.sync_dialogs()
        self.client.add_update_handler(self.update_catch)
        return self

    def stop(self):
        self.client.disconnect()
        self.client = None
        self.connected = False

    def sync_dialogs(self):
        dialogs = self.client.get_dialogs(limit=30)
        for dialog in dialogs:
            entity = dialog.entity
            if isinstance(entity, UserEntity):
                continue
            elif isinstance(entity, ChatEntity) and entity.deactivated:
                continue
            portal = po.Portal.get_by_entity(entity)
            portal.create_room(self, entity, invites=[self.mxid])
            # portal.update_info(self, entity)

    def update_catch(self, update):
        try:
            self.update(update)
        except:
            self.log.exception("Failed to handle Telegram update")

    def update(self, update):
        if isinstance(update, UpdateShortChatMessage):
            portal = po.Portal.get_by_tgid(update.chat_id, "chat")
            sender = pu.Puppet.get(update.from_id)
        elif isinstance(update, UpdateShortMessage):
            portal = po.Portal.get_by_tgid(update.user_id, "user")
            sender = pu.Puppet.get(self.tgid if update.out else update.user_id)
        else:
            self.log.debug("Unhandled update: %s", update)
            return

        if not portal.mxid:
            portal.create_room(self, invites=[self.mxid])
        self.log.debug("Handling message portal=%s sender=%s update=%s", portal, sender,
                       update)
        portal.handle_telegram_message(sender, update)

    @classmethod
    def get_by_mxid(cls, mxid, create=True):
        try:
            return cls.by_mxid[mxid]
        except KeyError:
            pass

        user = DBUser.query.get(mxid)
        if user:
            return cls.from_db(user).start()

        if create:
            user = cls(mxid)
            cls.db.add(user.to_db())
            cls.db.commit()
            return user.start()

        return None

    @classmethod
    def get_by_tgid(cls, tgid):
        try:
            return cls.by_tgid[tgid]
        except KeyError:
            pass

        user = DBUser.query.filter(DBUser.tgid == tgid).one_or_none()
        if user:
            return cls.from_db(user).start()

        return None


def init(context):
    global config
    User.az, User.db, log, config = context
    User.log = log.getChild("user")

    users = [User.from_db(user) for user in DBUser.query.all()]
    for user in users:
        user.start()
