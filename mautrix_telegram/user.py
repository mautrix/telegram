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
from io import BytesIO
from telethon import TelegramClient
from telethon.tl.types import *
from telethon.tl.functions.messages import SendMessageRequest
from .db import User as DBUser
from . import portal as po, puppet as pu

config = None


class User:
    log = None
    db = None
    az = None
    by_mxid = {}
    by_tgid = {}

    def __init__(self, mxid, tgid=None, username=None):
        self.mxid = mxid
        self.tgid = tgid
        self.username = username

        self.command_status = None
        self.connected = False
        self.client = None

        self.by_mxid[mxid] = self
        if tgid:
            self.by_tgid[tgid] = self

    @property
    def logged_in(self):
        return self.client.is_user_authorized()

    # region Database conversion

    def to_db(self):
        return self.db.merge(DBUser(mxid=self.mxid, tgid=self.tgid, tg_username=self.username))

    def save(self):
        self.to_db()
        self.db.commit()

    @classmethod
    def from_db(cls, db_user):
        return User(db_user.mxid, db_user.tgid, db_user.tg_username)

    # endregion
    # region Telegram connection management

    def start(self):
        self.client = TelegramClient(self.mxid,
                                     config["telegram.api_id"],
                                     config["telegram.api_hash"],
                                     update_workers=2)
        self.connected = self.client.connect()
        if self.logged_in:
            self.sync_dialogs()
            self.update_info()
        self.client.add_update_handler(self.update_catch)
        return self

    def stop(self):
        self.client.disconnect()
        self.client = None
        self.connected = False

    # endregion
    # region Telegram actions that need custom methods

    def update_info(self, info=None):
        info = info or self.client.get_me()
        changed = False
        self.username = info.username
        if self.tgid != info.id:
            self.tgid = info.id
            self.by_tgid[self.tgid] = self
        if changed:
            self.save()

    def log_out(self):
        self.connected = False
        if self.tgid:
            try:
                del self.tgid[self.tgid]
            except KeyError:
                pass
        return self.client.log_out()

    def send_message(self, entity, message, reply_to=None, entities=None, link_preview=True):
        entity = self.client.get_input_entity(entity)

        request = SendMessageRequest(
            peer=entity,
            message=message,
            entities=entities,
            no_webpage=not link_preview,
            reply_to_msg_id=self.client._get_reply_to(reply_to)
        )
        result = self.client(request)
        if isinstance(result, UpdateShortSentMessage):
            return Message(
                id=result.id,
                to_id=entity,
                message=message,
                date=result.date,
                out=result.out,
                media=result.media,
                entities=result.entities
            )

        return self.client._get_response_message(request, result)

    def download_file(self, location):
        if not isinstance(location, InputFileLocation):
            location = InputFileLocation(location.volume_id, location.local_id, location.secret)

        file = BytesIO()

        self.client.download_file(location, file)

        data = file.getvalue()
        file.close()
        return data

    def sync_dialogs(self):
        dialogs = self.client.get_dialogs(limit=30)
        for dialog in dialogs:
            entity = dialog.entity
            if isinstance(entity, User):
                continue
            elif isinstance(entity, Chat) and entity.deactivated:
                continue
            portal = po.Portal.get_by_entity(entity)
            portal.create_room(self, entity, invites=[self.mxid])

    # endregion
    # region Telegram update handling

    def update_catch(self, update):
        try:
            self.update(update)
        except:
            self.log.exception("Failed to handle Telegram update")

    def update(self, update):
        update_type = type(update)

        if update_type in {UpdateShortChatMessage, UpdateShortMessage, UpdateNewMessage,
                           UpdateNewChannelMessage}:
            return self.update_message(update)
        elif update_type in {UpdateChatUserTyping, UpdateUserTyping}:
            return self.update_typing(update)
        elif update_type == UpdateUserStatus:
            return self.update_status(update)
        else:
            self.log.debug("Unhandled update: %s", update)
            return

    def get_message_details(self, update):
        update_type = type(update)
        if update_type == UpdateShortChatMessage:
            portal = po.Portal.get_by_tgid(update.chat_id, "chat")
            sender = pu.Puppet.get(update.from_id)
        elif update_type == UpdateShortMessage:
            portal = po.Portal.get_by_tgid(update.user_id, "user")
            sender = pu.Puppet.get(self.tgid if update.out else update.user_id)
        elif update_type in {UpdateNewMessage, UpdateNewChannelMessage}:
            update = update.message
            sender = pu.Puppet.get(update.from_id)
            portal = po.Portal.get_by_entity(update.to_id)
        return update, sender, portal

    def update_typing(self, update):
        update_type = type(update)
        if update_type == UpdateUserTyping:
            portal = po.Portal.get_by_tgid(update.user_id, "user")
        else:
            portal = po.Portal.get_by_tgid(update.chat_id, "chat")
        sender = pu.Puppet.get(update.user_id)
        return portal.handle_telegram_typing(sender, update)

    def update_status(self, update):
        puppet = pu.Puppet.get(update.user_id)
        status = type(update.status)
        if status == UserStatusOnline:
            puppet.intent.set_presence("online")
        elif status == UserStatusOffline:
            puppet.intent.set_presence("offline")
        return

    def update_message(self, update):
        update, sender, portal = self.get_message_details(update)

        if isinstance(update, MessageService):
            self.log.debug("Handling action %s to %d by %d", update.action, portal.tgid, sender.id)
            portal.handle_telegram_action(self, sender, update.action)
        else:
            self.log.debug("Handling message %s to %d by %d", update, portal.tgid, sender.tgid)
            portal.handle_telegram_message(self, sender, update)

    # endregion
    # region Class instance lookup

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

    @classmethod
    def find_by_username(cls, username):
        for _, user in cls.by_tgid.items():
            if user.username == username:
                return user

        puppet = DBUser.query.filter(DBUser.tg_username == username).one_or_none()
        if puppet:
            return cls.from_db(puppet)

        return None
    # endregion


def init(context):
    global config
    User.az, User.db, log, config = context
    User.log = log.getChild("user")

    users = [User.from_db(user) for user in DBUser.query.all()]
    for user in users:
        user.start()
