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
from telethon.tl.types import ChannelParticipantsRecent, PeerChat, PeerChannel, PeerUser
from .db import Portal as DBPortal
from . import puppet as p, formatter

config = None


class Portal:
    by_mxid = {}
    by_tgid = {}

    def __init__(self, tgid, peer_type, mxid=None):
        self.mxid = mxid
        self.tgid = tgid
        self.peer_type = peer_type

        self.by_tgid[tgid] = self
        if mxid:
            self.by_mxid[mxid] = self

    def create_room(self, user, entity=None, invites=[]):
        self.log.debug("Creating room for %d", self.tgid)
        if not entity:
            entity = user.client.get_entity(self.peer)
            self.log.debug("Fetched data: %s", entity)

        if self.mxid:
            self.invite_matrix(invites)
            users = self.get_users(user, entity)
            self.sync_telegram_users(users)
            return self.mxid

        try:
            title = entity.title
        except AttributeError:
            title = None

        direct = self.peer_type == "user"
        puppet = p.Puppet.get(self.tgid) if direct else None
        intent = puppet.intent if direct else self.az.intent
        room = intent.create_room(invitees=invites, name=title,
                                          is_direct=direct)
        if not room:
            raise Exception(f"Failed to create room for {self.tgid}")

        self.mxid = room["room_id"]
        self.by_mxid[self.mxid] = self
        self.save()
        if not direct:
            users = self.get_users(user, entity)
            self.sync_telegram_users(users)
        else:
            puppet.update_info(entity)
            puppet.intent.join_room(self.mxid)

    def sync_telegram_users(self, users=[]):
        for entity in users:
            user = p.Puppet.get(entity.id)
            user.update_info(entity)
            user.intent.join_room(self.mxid)

    def handle_matrix_message(self, sender, message):
        type = message["msgtype"]
        if type == "m.text":
            if "format" in message and message["format"] == "org.matrix.custom.html":
                message, entities = formatter.matrix_to_telegram(message["formatted_body"])
                sender.send_message(self.peer, message, entities=entities)
            else:
                sender.send_message(self.peer, message["body"])

    def handle_telegram_message(self, sender, evt):
        self.log.debug("Sending %s to %s by %d", evt.message, self.mxid, sender.id)
        if evt.message:
            if evt.entities:
                html = formatter.telegram_to_matrix(evt.message, evt.entities)
                sender.intent.send_text(self.mxid, evt.message, html=html)
            else:
                sender.intent.send_text(self.mxid, evt.message)

    @property
    def peer(self):
        if self.peer_type == "user":
            return PeerUser(user_id=self.tgid)
        elif self.peer_type == "chat":
            return PeerChat(chat_id=self.tgid)
        elif self.peer_type == "channel":
            return PeerChannel(channel_id=self.tgid)

    def get_users(self, user, entity):
        if self.peer_type == "chat":
            return user.client(GetFullChatRequest(chat_id=self.tgid)).users
        elif self.peer_type == "channel":
            participants = user.client(GetParticipantsRequest(
                entity, ChannelParticipantsRecent(), offset=0, limit=100, hash=0
            ))
            return participants.users
        elif self.peer_type == "user":
            return [entity]

    def invite_matrix(self, users=[]):
        pass

    def to_db(self):
        return self.db.merge(DBPortal(tgid=self.tgid, peer_type=self.peer_type, mxid=self.mxid))

    def save(self):
        self.to_db()
        self.db.commit()

    @classmethod
    def from_db(cls, db_portal):
        return Portal(db_portal.tgid, db_portal.peer_type, db_portal.mxid)

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
        return cls.get_by_tgid(entity.id, entity.__class__.__name__.lower())


def init(context):
    global config
    Portal.az, Portal.db, log, config = context
    Portal.log = log.getChild("portal")
