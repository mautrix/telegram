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
from telethon.tl.functions.messages import GetFullChatRequest
from telethon.tl.functions.channels import GetParticipantsRequest
from telethon.errors.rpc_error_list import ChatAdminRequiredError
from telethon.tl.types import *
from .db import Portal as DBPortal, Message as DBMessage
from . import puppet as p, formatter

config = None


class Portal:
    log = None
    db = None
    az = None
    by_mxid = {}
    by_tgid = {}

    def __init__(self, tgid, peer_type, mxid=None, username=None, title=None, photo_id=None):
        self.mxid = mxid
        self.tgid = tgid
        self.peer_type = peer_type
        self.username = username
        self.title = title
        self.photo_id = photo_id
        self._main_intent = None

        self.by_tgid[tgid] = self
        if mxid:
            self.by_mxid[mxid] = self

    @property
    def peer(self):
        if self.peer_type == "user":
            return PeerUser(user_id=self.tgid)
        elif self.peer_type == "chat":
            return PeerChat(chat_id=self.tgid)
        elif self.peer_type == "channel":
            return PeerChannel(channel_id=self.tgid)

    # region Matrix room info updating

    @property
    def main_intent(self):
        if not self._main_intent:
            direct = self.peer_type == "user"
            puppet = p.Puppet.get(self.tgid) if direct else None
            self._main_intent = puppet.intent if direct else self.az.intent
        return self._main_intent

    def invite_matrix(self, users=[]):
        if isinstance(users, str):
            self.main_intent.invite(self.mxid, users)
        else:
            for user in users:
                self.main_intent.invite(self.mxid, user)

    def create_room(self, user, entity=None, invites=[], update_if_exists=True):
        if not entity:
            entity = user.client.get_entity(self.peer)
            self.log.debug("Fetched data: %s", entity)

        if self.mxid:
            if update_if_exists:
                self.update_info(user, entity)
                users = self.get_users(user, entity)
                self.sync_telegram_users(user, users)
            self.invite_matrix(invites)
            return self.mxid

        self.log.debug("Creating room for %d", self.tgid)

        try:
            title = entity.title
        except AttributeError:
            title = None

        direct = self.peer_type == "user"
        puppet = p.Puppet.get(self.tgid) if direct else None
        intent = puppet.intent if direct else self.az.intent
        # TODO set room alias if public channel.
        room = intent.create_room(invitees=invites, name=title,
                                  is_direct=direct)
        if not room:
            raise Exception(f"Failed to create room for {self.tgid}")

        self.mxid = room["room_id"]
        self.by_mxid[self.mxid] = self
        self.save()
        if not direct:
            self.update_info(user, entity)
            users = self.get_users(user, entity)
            self.sync_telegram_users(user, users)
        else:
            puppet.update_info(user, entity)
            puppet.intent.join_room(self.mxid)

    def sync_telegram_users(self, source, users=[]):
        for entity in users:
            puppet = p.Puppet.get(entity.id)
            puppet.update_info(source, entity)
            puppet.intent.join_room(self.mxid)

    def add_telegram_user(self, user_id, source=None):
        puppet = p.Puppet.get(user_id)
        if source:
            entity = source.client.get_entity(user_id)
            puppet.update_info(source, entity)
        puppet.intent.join_room(self.mxid)

    def delete_telegram_user(self, user_id):
        puppet = p.Puppet.get(user_id)
        puppet.intent.leave_room(self.mxid)

    def update_info(self, user, entity=None):
        if self.peer_type == "user":
            self.log.warn("Called update_info() for direct chat portal %d", self.tgid)
            return

        self.log.debug("Updating info of %d", self.tgid)
        if not entity:
            entity = user.client.get_entity(self.peer)
            self.log.debug("Fetched data: %s", entity)
        changed = False

        if self.peer_type == "channel":
            if self.username != entity.username:
                # TODO update room alias
                self.username = entity.username
                changed = True

        changed = self.update_title(entity.title, self.main_intent) or changed

        if isinstance(entity.photo, ChatPhoto):
            changed = self.update_avatar(user, entity.photo.photo_big, self.main_intent) or changed

        if changed:
            self.save()

    def update_title(self, title, intent=None):
        if self.title != title:
            self.title = title
            self.main_intent.set_room_name(self.mxid, self.title)
            return True
        return False

    def update_avatar(self, user, photo, intent=None):
        photo_id = f"{photo.volume_id}-{photo.local_id}"
        if self.photo_id != photo_id:
            file = user.download_file(photo)
            uploaded = self.main_intent.media_upload(file)
            self.main_intent.set_room_avatar(self.mxid, uploaded["content_uri"])
            self.photo_id = photo_id
            return True
        return False

    def get_users(self, user, entity):
        if self.peer_type == "chat":
            return user.client(GetFullChatRequest(chat_id=self.tgid)).users
        elif self.peer_type == "channel":
            try:
                participants = user.client(GetParticipantsRequest(
                    entity, ChannelParticipantsRecent(), offset=0, limit=100, hash=0
                ))
                return participants.users
            except ChatAdminRequiredError:
                return []
        elif self.peer_type == "user":
            return [entity]

    # endregion
    # region Matrix event handling

    def handle_matrix_message(self, sender, message, event_id):
        type = message["msgtype"]
        if type == "m.text":
            if "format" in message and message["format"] == "org.matrix.custom.html":
                message, entities = formatter.matrix_to_telegram(message["formatted_body"],
                                                                 sender.tgid)
                reply_to = None
                if len(entities) > 0 and isinstance(entities[0], formatter.MessageEntityReply):
                    reply = entities.pop(0)
                    # message = message[:reply.offset] + message[reply.offset + reply.length:]
                    reply_to = reply.msg_id
                response = sender.send_message(self.peer, message, entities=entities,
                                               reply_to=reply_to)
            else:
                response = sender.send_message(self.peer, message["body"])
            self.db.add(
                DBMessage(tgid=response.id, mx_room=self.mxid, mxid=event_id, user=sender.tgid))
            self.db.commit()

    # endregion
    # region Telegram event handling

    def handle_telegram_typing(self, user, event):
        user.intent.set_typing(self.mxid, is_typing=True)

    def handle_telegram_message(self, source, sender, evt):
        if not self.mxid:
            self.create_room(source, invites=[source.mxid])

        if evt.message:
            self.log.debug("Sending %s to %s by %d", evt.message, self.mxid, sender.id)
            text, html = formatter.telegram_event_to_matrix(evt, source)
            response = sender.intent.send_text(self.mxid, text, html=html)
            self.db.add(DBMessage(tgid=evt.id, mx_room=self.mxid, mxid=response["event_id"],
                                  user=source.tgid))
            self.db.commit()
        else:
            self.log.debug("Unhandled Telegram message: %s", evt)

    def handle_telegram_action(self, source, sender, action):
        action_type = type(action)
        if not self.mxid:
            if action_type in {MessageActionChatCreate, MessageActionChannelCreate}:
                self.create_room(source, invites=[source.mxid])
            return

        if action_type == MessageActionChatEditTitle:
            if self.update_title(action.title, self.main_intent):
                self.save()
        elif action_type == MessageActionChatEditPhoto:
            largest_size = max(action.photo.sizes, key=lambda photo: photo.size)
            if self.update_avatar(source, largest_size.location, self.main_intent):
                self.save()
        elif action_type == MessageActionChatAddUser:
            for id in action.users:
                self.add_telegram_user(id, source)
        elif action_type == MessageActionChatJoinedByLink:
            self.add_telegram_user(sender.id, source)
        elif action_type == MessageActionChatDeleteUser:
            # TODO show kick message if user was kicked
            self.delete_telegram_user(action.user_id)
        else:
            self.log.debug("Unhandled Telegram action in %s: %s", self.title, action)

    # endregion
    # region Database conversion

    def to_db(self):
        return self.db.merge(DBPortal(tgid=self.tgid, peer_type=self.peer_type, mxid=self.mxid,
                                      username=self.username, title=self.title,
                                      photo_id=self.photo_id))

    def save(self):
        self.to_db()
        self.db.commit()

    @classmethod
    def from_db(cls, db_portal):
        return Portal(db_portal.tgid, db_portal.peer_type, db_portal.mxid, db_portal.username,
                      db_portal.title, db_portal.photo_id)

    # endregion
    # region Class instance lookup

    @classmethod
    def get_by_mxid(cls, mxid):
        try:
            return cls.by_mxid[mxid]
        except KeyError:
            pass

        portal = DBPortal.query.filter(DBPortal.mxid == mxid).one_or_none()
        if portal:
            return cls.from_db(portal)

        return None

    @classmethod
    def get_by_tgid(cls, tgid, peer_type=None):
        try:
            return cls.by_tgid[tgid]
        except KeyError:
            pass

        portal = DBPortal.query.get(tgid)
        if portal:
            return cls.from_db(portal)

        if peer_type:
            portal = Portal(tgid, peer_type)
            cls.db.add(portal.to_db())
            portal.save()
            return portal

        return None

    @classmethod
    def get_by_entity(cls, entity):
        entity_type = type(entity)
        if entity_type in {Chat, ChatFull}:
            type_name = "chat"
            id = entity.id
        elif entity_type in {PeerChat, InputPeerChat}:
            type_name = "chat"
            id = entity.chat_id
        elif entity_type in {Channel, ChannelFull}:
            type_name = "channel"
            id = entity.id
        elif entity_type in {PeerChannel, InputPeerChannel, InputChannel}:
            type_name = "channel"
            id = entity.channel_id
        elif entity_type in {User, UserFull}:
            type_name = "user"
            id = entity.id
        elif entity_type in {PeerUser, InputPeerUser, InputUser}:
            type_name = "user"
            id = entity.user_id
        else:
            raise ValueError(f"Unknown entity type {entity_type.__name__}")
        return cls.get_by_tgid(id, type_name)

    # endregion


def init(context):
    global config
    Portal.az, Portal.db, log, config = context
    Portal.log = log.getChild("portal")
