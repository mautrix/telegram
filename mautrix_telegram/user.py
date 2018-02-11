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
import logging
import asyncio
import platform

from telethon.tl.types import *
from telethon.tl.types.contacts import ContactsNotModified
from telethon.tl.types import User as TLUser
from telethon.tl.functions.contacts import GetContactsRequest, SearchRequest

from .db import User as DBUser, Message as DBMessage, Contact as DBContact
from .tgclient import MautrixTelegramClient
from . import portal as po, puppet as pu, __version__

config = None


class User:
    loop = None
    log = logging.getLogger("mau.user")
    db = None
    az = None
    by_mxid = {}
    by_tgid = {}

    def __init__(self, mxid, tgid=None, username=None, db_contacts=None, saved_contacts=0):
        self.mxid = mxid
        self.tgid = tgid
        self.username = username
        self.contacts = []
        self.saved_contacts = saved_contacts
        self.db_contacts = db_contacts

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

    @property
    def db_contacts(self):
        return [self.db.merge(DBContact(user=self.tgid, contact=puppet.id))
                for puppet in self.contacts]

    @db_contacts.setter
    def db_contacts(self, contacts):
        if contacts:
            self.contacts = [pu.Puppet.get(entry.contact) for entry in contacts]
        else:
            self.contacts = []

    def get_input_entity(self, user):
        return user.client.get_input_entity(InputUser(user_id=self.tgid, access_hash=0))

    # region Database conversion

    def to_db(self):
        return self.db.merge(
            DBUser(mxid=self.mxid, tgid=self.tgid, tg_username=self.username,
                   contacts=self.db_contacts, saved_contacts=self.saved_contacts))

    def save(self):
        self.to_db()
        self.db.commit()

    @classmethod
    def from_db(cls, db_user):
        return User(db_user.mxid, db_user.tgid, db_user.tg_username, db_user.contacts,
                    db_user.saved_contacts)

    # endregion
    # region Telegram connection management

    async def start(self):
        device = f"{platform.system()} {platform.release()}"
        sysversion = MautrixTelegramClient.__version__
        self.client = MautrixTelegramClient(self.mxid,
                                            config["telegram.api_id"],
                                            config["telegram.api_hash"],
                                            loop=self.loop,
                                            app_version=__version__,
                                            system_version=sysversion,
                                            device_model=device)
        self.client.add_update_handler(self.update_catch)
        self.connected = await self.client.connect()
        if self.logged_in:
            asyncio.ensure_future(self.post_login(), loop=self.loop)
        return self

    async def post_login(self, info=None):
        try:
            await self.update_info(info)
            await self.sync_dialogs()
            await self.sync_contacts()
        except Exception:
            self.log.exception("Failed to run post-login functions")

    def stop(self):
        self.client.disconnect()
        self.client = None
        self.connected = False

    # endregion
    # region Telegram actions that need custom methods

    async def update_info(self, info=None):
        info = info or await self.client.get_me()
        changed = False
        if self.username != info.username:
            self.username = info.username
            changed = True
        if self.tgid != info.id:
            self.tgid = info.id
            self.by_tgid[self.tgid] = self
        if changed:
            self.save()

    async def log_out(self):
        self.connected = False
        if self.tgid:
            try:
                del self.by_tgid[self.tgid]
            except KeyError:
                pass
            self.tgid = None
            self.save()
        await self.client.log_out()
        # TODO kick user from portals

    def _search_local(self, query, max_results=5, min_similarity=45):
        results = []
        for contact in self.contacts:
            similarity = contact.similarity(query)
            if similarity >= min_similarity:
                results.append((contact, similarity))
        results.sort(key=lambda tup: tup[1], reverse=True)
        return results[0:max_results]

    async def _search_remote(self, query, max_results=5):
        if len(query) < 5:
            return []
        server_results = await self.client(SearchRequest(q=query, limit=max_results))
        results = []
        for user in server_results.users:
            puppet = pu.Puppet.get(user.id)
            await puppet.update_info(self, user)
            results.append((puppet, puppet.similarity(query)))
        results.sort(key=lambda tup: tup[1], reverse=True)
        return results[0:max_results]

    async def search(self, query, force_remote=False):
        if force_remote:
            return await self._search_remote(query), True

        results = self._search_local(query)
        if results:
            return results, False

        return await self._search_remote(query), True

    async def sync_dialogs(self):
        dialogs = await self.client.get_dialogs(limit=30)
        creators = []
        for dialog in dialogs:
            entity = dialog.entity
            invalid = (isinstance(entity, (TLUser, ChatForbidden, ChannelForbidden))
                       or (isinstance(entity, Chat) and (entity.deactivated or entity.left)))
            if invalid:
                continue
            portal = po.Portal.get_by_entity(entity)
            creators.append(portal.create_matrix_room(self, entity, invites=[self.mxid]))
        await asyncio.gather(*creators, loop=self.loop)

    def _hash_contacts(self):
        acc = 0
        for id in sorted([self.saved_contacts] + [contact.id for contact in self.contacts]):
            acc = (acc * 20261 + id) & 0xffffffff
        return acc & 0x7fffffff

    async def sync_contacts(self):
        response = await self.client(GetContactsRequest(hash=self._hash_contacts()))
        if isinstance(response, ContactsNotModified):
            return
        self.log.debug("Updating contacts...")
        self.contacts = []
        self.saved_contacts = response.saved_count
        for user in response.users:
            puppet = pu.Puppet.get(user.id)
            await puppet.update_info(self, user)
            self.contacts.append(puppet)
        self.save()

    # endregion
    # region Telegram update handling

    async def update_catch(self, update):
        try:
            await self.update(update)
        except Exception:
            self.log.exception("Failed to handle Telegram update")

    async def update(self, update):
        if isinstance(update, (UpdateShortChatMessage, UpdateShortMessage, UpdateNewMessage,
                               UpdateNewChannelMessage)):
            await self.update_message(update)
        elif isinstance(update, (UpdateChatUserTyping, UpdateUserTyping)):
            await self.update_typing(update)
        elif isinstance(update, UpdateUserStatus):
            await self.update_status(update)
        elif isinstance(update, (UpdateChatAdmins, UpdateChatParticipantAdmin)):
            await self.update_admin(update)
        elif isinstance(update, UpdateChatParticipants):
            portal = po.Portal.get_by_tgid(update.participants.chat_id)
            if portal and portal.mxid:
                await portal.update_telegram_participants(update.participants.participants)
        elif isinstance(update, UpdateChannelPinnedMessage):
            portal = po.Portal.get_by_tgid(update.channel_id)
            if portal and portal.mxid:
                await portal.update_telegram_pin(self, update.id)
        elif isinstance(update, (UpdateUserName, UpdateUserPhoto)):
            await self.update_others_info(update)
        elif isinstance(update, UpdateReadHistoryOutbox):
            await self.update_read_receipt(update)
        else:
            self.log.debug("Unhandled update: %s", update)

    async def update_read_receipt(self, update):
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
        await puppet.intent.mark_read(portal.mxid, message.mxid)

    async def update_admin(self, update):
        portal = po.Portal.get_by_tgid(update.chat_id, peer_type="chat")
        if isinstance(update, UpdateChatAdmins):
            await portal.set_telegram_admins_enabled(update.enabled)
        elif isinstance(update, UpdateChatParticipantAdmin):
            puppet = pu.Puppet.get(update.user_id)
            user = User.get_by_tgid(update.user_id)
            await portal.set_telegram_admin(puppet, user)

    async def update_typing(self, update):
        if isinstance(update, UpdateUserTyping):
            portal = po.Portal.get_by_tgid(update.user_id, self.tgid, "user")
        else:
            portal = po.Portal.get_by_tgid(update.chat_id, peer_type="chat")
        sender = pu.Puppet.get(update.user_id)
        await portal.handle_telegram_typing(sender, update)

    async def update_others_info(self, update):
        puppet = pu.Puppet.get(update.user_id)
        if isinstance(update, UpdateUserName):
            if await puppet.update_displayname(self, update):
                puppet.save()
        elif isinstance(update, UpdateUserPhoto):
            if await puppet.update_avatar(self, update.photo.photo_big):
                puppet.save()

    async def update_status(self, update):
        puppet = pu.Puppet.get(update.user_id)
        if isinstance(update.status, UserStatusOnline):
            await puppet.intent.set_presence("online")
        elif isinstance(update.status, UserStatusOffline):
            await puppet.intent.set_presence("offline")
        else:
            self.log.warning("Unexpected user status update: %s", update)
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

    async def update_message(self, update):
        update, sender, portal = self.get_message_details(update)

        if isinstance(update, MessageService):
            if isinstance(update.action, MessageActionChannelMigrateFrom):
                self.log.debug(f"Ignoring action %s to %s by %d", update.action, portal.tgid_log,
                               sender.id)
                return
            self.log.debug("Handling action %s to %s by %d", update.action, portal.tgid_log,
                           sender.id)
            await portal.handle_telegram_action(self, sender, update.action)
        else:
            self.log.debug("Handling message %s to %s by %d", update, portal.tgid_log, sender.tgid)
            await portal.handle_telegram_message(self, sender, update)

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
            user = cls.from_db(user)
            asyncio.ensure_future(user.start(), loop=cls.loop)
            return user

        if create:
            user = cls(mxid)
            cls.db.add(user.to_db())
            cls.db.commit()
            asyncio.ensure_future(user.start(), loop=cls.loop)
            return user

        return None

    @classmethod
    def get_by_tgid(cls, tgid):
        try:
            return cls.by_tgid[tgid]
        except KeyError:
            pass

        user = DBUser.query.filter(DBUser.tgid == tgid).one_or_none()
        if user:
            user = cls.from_db(user)
            asyncio.ensure_future(user.start(), loop=cls.loop)
            return user

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
    User.az, User.db, config, User.loop = context

    users = [User.from_db(user) for user in DBUser.query.all()]
    return [user.start() for user in users]
