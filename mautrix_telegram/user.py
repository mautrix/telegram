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
from telethon.tl.types import *
from telethon.tl.types import User as TLUser
from .db import User as DBUser, Message as DBMessage
from .tgclient import MautrixTelegramClient
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

        self.is_admin = self.mxid in config.get("bridge.admins", [])

        whitelist = config.get("bridge.whitelist", None) or [self.mxid]
        self.whitelisted = not whitelist or self.mxid in whitelist
        if not self.whitelisted:
            homeserver = self.mxid[self.mxid.index(":") + 1:]
            self.whitelisted = homeserver in whitelist

        self.by_mxid[mxid] = self
        if tgid:
            self.by_tgid[tgid] = self

    @property
    def logged_in(self):
        return self.client.is_user_authorized()

    @property
    def has_full_access(self):
        return self.logged_in and self.whitelisted

    def get_input_entity(self, user):
        return user.client.get_input_entity(InputUser(user_id=self.tgid, access_hash=0))

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
        self.client = MautrixTelegramClient(self.mxid,
                                            config["telegram.api_id"],
                                            config["telegram.api_hash"],
                                            update_workers=2)
        self.connected = self.client.connect()
        if self.logged_in:
            self.post_login()
        self.client.add_update_handler(self.update_catch)
        return self

    def post_login(self, info=None):
        self.sync_dialogs()
        self.update_info(info)

    def stop(self):
        self.client.disconnect()
        self.client = None
        self.connected = False

    # endregion
    # region Telegram actions that need custom methods

    def update_info(self, info=None):
        info = info or self.client.get_me()
        changed = False
        if self.username != info.username:
            self.username = info.username
            changed = True
        if self.tgid != info.id:
            self.tgid = info.id
            self.by_tgid[self.tgid] = self
        if changed:
            self.save()

    def log_out(self):
        self.connected = False
        if self.tgid:
            try:
                del self.by_tgid[self.tgid]
            except KeyError:
                pass
            self.tgid = None
            self.save()
        return self.client.log_out()

    def sync_dialogs(self):
        dialogs = self.client.get_dialogs(limit=30)
        for dialog in dialogs:
            entity = dialog.entity
            if (isinstance(entity, (TLUser, ChatForbidden, ChannelForbidden)) or (
                isinstance(entity, Chat) and (entity.deactivated or entity.left))):
                continue
            portal = po.Portal.get_by_entity(entity)
            portal.create_matrix_room(self, entity, invites=[self.mxid])

    # endregion
    # region Telegram update handling

    def update_catch(self, update):
        try:
            self.update(update)
        except Exception:
            self.log.exception("Failed to handle Telegram update")

    def update(self, update):
        if isinstance(update, (UpdateShortChatMessage, UpdateShortMessage, UpdateNewMessage,
                               UpdateNewChannelMessage)):
            self.update_message(update)
        elif isinstance(update, (UpdateChatUserTyping, UpdateUserTyping)):
            self.update_typing(update)
        elif isinstance(update, UpdateUserStatus):
            self.update_status(update)
        elif isinstance(update, (UpdateChatAdmins, UpdateChatParticipantAdmin)):
            self.update_admin(update)
        elif isinstance(update, UpdateChatParticipants):
            portal = po.Portal.get_by_tgid(update.participants.chat_id)
            if portal and portal.mxid:
                portal.update_telegram_participants(update.participants.participants)
        elif isinstance(update, UpdateChannelPinnedMessage):
            portal = po.Portal.get_by_tgid(update.channel_id, peer_type="channel")
            if portal and portal.mxid:
                portal.update_telegram_pin(self, update.id)
        elif isinstance(update, (UpdateUserName, UpdateUserPhoto)):
            self.update_others_info(update)
        elif isinstance(update, UpdateReadHistoryOutbox):
            self.update_read_receipt(update)
        else:
            self.log.debug("Unhandled update: %s", update)

    def update_read_receipt(self, update):
        if not isinstance(update.peer, PeerUser):
            self.log.debug("Unexpected read receipt peer: %s", update.peer)
            return

        portal = po.Portal.get_by_tgid(update.peer.user_id, self.tgid)
        if not portal or not portal.mxid:
            return

        # We check that these are user read receipts, so tg_space is always the user ID.
        message = DBMessage.query.get((update.max_id, self.tgid))
        if not message:
            return

        puppet = pu.Puppet.get(update.peer.user_id)
        puppet.intent.mark_read(portal.mxid, message.mxid)

    def update_admin(self, update):
        portal = po.Portal.get_by_tgid(update.chat_id, peer_type="chat")
        if isinstance(update, UpdateChatAdmins):
            portal.set_telegram_admins_enabled(update.enabled)
        elif isinstance(update, UpdateChatParticipantAdmin):
            puppet = pu.Puppet.get(update.user_id)
            user = User.get_by_tgid(update.user_id)
            portal.set_telegram_admin(puppet, user)

    def update_typing(self, update):
        if isinstance(update, UpdateUserTyping):
            portal = po.Portal.get_by_tgid(update.user_id, self.tgid, "user")
        else:
            portal = po.Portal.get_by_tgid(update.chat_id, peer_type="chat")
        sender = pu.Puppet.get(update.user_id)
        return portal.handle_telegram_typing(sender, update)

    def update_others_info(self, update):
        puppet = pu.Puppet.get(update.user_id)
        if isinstance(update, UpdateUserName):
            if puppet.update_displayname(self, update):
                puppet.save()
        elif isinstance(update, UpdateUserPhoto):
            if puppet.update_avatar(self, update.photo.photo_big):
                puppet.save()

    def update_status(self, update):
        puppet = pu.Puppet.get(update.user_id)
        if isinstance(update.status, UserStatusOnline):
            puppet.intent.set_presence("online")
        elif isinstance(update.status, UserStatusOffline):
            puppet.intent.set_presence("offline")
        return

    def get_message_details(self, update):
        if isinstance(update, UpdateShortChatMessage):
            portal = po.Portal.get_by_tgid(update.chat_id, peer_type="chat")
            sender = pu.Puppet.get(update.from_id)
        elif isinstance(update, UpdateShortMessage):
            portal = po.Portal.get_by_tgid(update.user_id, self.tgid, "user")
            sender = pu.Puppet.get(self.tgid if update.out else update.user_id)
        elif isinstance(update, (UpdateNewMessage, UpdateNewChannelMessage)):
            update = update.message
            if isinstance(update.to_id, PeerUser) and not update.out:
                portal = po.Portal.get_by_tgid(update.from_id, peer_type="user",
                                               tg_receiver=self.tgid)
            else:
                portal = po.Portal.get_by_entity(update.to_id, receiver_id=self.tgid)
            sender = pu.Puppet.get(update.from_id)
        else:
            self.log.warning(
                f"Unexpected message type in User#get_message_details: {type(update)}")
            return update, None, None
        return update, sender, portal

    def update_message(self, update):
        update, sender, portal = self.get_message_details(update)

        if isinstance(update, MessageService):
            if isinstance(update.action, MessageActionChannelMigrateFrom):
                self.log.debug(f"Ignoring action %s to %s by %d", update.action, portal.tgid_log,
                               sender.id)
                return
            self.log.debug("Handling action %s to %s by %d", update.action, portal.tgid_log,
                           sender.id)
            portal.handle_telegram_action(self, sender, update.action)
        else:
            self.log.debug("Handling message %s to %s by %d", update, portal.tgid_log, sender.tgid)
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
