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
import logging
import asyncio
import re

from telethon.tl.types import *
from telethon.tl.types.contacts import ContactsNotModified
from telethon.tl.functions.contacts import GetContactsRequest, SearchRequest
from mautrix_appservice import MatrixRequestError

from .db import User as DBUser, Contact as DBContact
from .abstract_user import AbstractUser
from . import portal as po, puppet as pu

config = None


class User(AbstractUser):
    log = logging.getLogger("mau.user")
    by_mxid = {}
    by_tgid = {}

    def __init__(self, mxid, tgid=None, username=None, db_contacts=None, saved_contacts=0,
                 db_portals=None, db_instance=None):
        super().__init__()
        self.mxid = mxid
        self.tgid = tgid
        self.username = username
        self.contacts = []
        self.saved_contacts = saved_contacts
        self.db_contacts = db_contacts
        self.portals = {}
        self.db_portals = db_portals
        self._db_instance = db_instance

        self.command_status = None

        (self.relaybot_whitelisted,
         self.whitelisted,
         self.is_admin) = config.get_permissions(self.mxid)

        self.by_mxid[mxid] = self
        if tgid:
            self.by_tgid[tgid] = self

    @property
    def name(self):
        return self.mxid

    @property
    def displayname(self):
        # TODO show better username
        match = re.compile("@(.+):(.+)").match(self.mxid)
        return match.group(1)

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

    @property
    def db_portals(self):
        return [portal.db_instance for portal in self.portals.values()]

    @db_portals.setter
    def db_portals(self, portals):
        if portals:
            self.portals = {(portal.tgid, portal.tg_receiver):
                                po.Portal.get_by_tgid(portal.tgid, portal.tg_receiver)
                            for portal in portals}
        else:
            self.portals = {}

    # region Database conversion

    @property
    def db_instance(self):
        if not self._db_instance:
            self._db_instance = self.new_db_instance()
        return self._db_instance

    def new_db_instance(self):
        return DBUser(mxid=self.mxid, tgid=self.tgid, tg_username=self.username,
                      contacts=self.db_contacts, saved_contacts=self.saved_contacts,
                      portals=self.db_portals)

    def save(self):
        self.db_instance.tgid = self.tgid
        self.db_instance.username = self.username
        self.db_instance.contacts = self.db_contacts
        self.db_instance.saved_contacts = self.saved_contacts
        self.db_instance.portals = self.db_portals
        self.db.commit()

    def delete(self):
        try:
            del self.by_mxid[self.mxid]
            del self.by_tgid[self.tgid]
        except KeyError:
            pass
        if self._db_instance:
            self.db.delete(self._db_instance)
            self.db.commit()

    @classmethod
    def from_db(cls, db_user):
        return User(db_user.mxid, db_user.tgid, db_user.tg_username, db_user.contacts,
                    db_user.saved_contacts, db_user.portals, db_instance=db_user)

    # endregion
    # region Telegram connection management

    async def start(self, delete_unless_authenticated=False):
        await super().start()
        if self.logged_in:
            self.log.debug(f"Ensuring post_login() for {self.name}")
            asyncio.ensure_future(self.post_login(), loop=self.loop)
        elif delete_unless_authenticated:
            self.log.debug(f"Unauthenticated user {self.name} start()ed, deleting...")
            # User not logged in -> forget user
            self.client.disconnect()
            # self.client.session.delete()
            self.delete()
        return self

    async def post_login(self, info=None):
        try:
            await self.update_info(info)
            await self.sync_dialogs()
            await self.sync_contacts()
            if config["bridge.catch_up"]:
                await self.client.catch_up()
        except Exception:
            self.log.exception("Failed to run post-login functions")

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
        for _, portal in self.portals.items():
            if portal.has_bot:
                continue
            try:
                await portal.main_intent.kick(portal.mxid, self.mxid, "Logged out of Telegram.")
            except MatrixRequestError:
                pass
        self.portals = {}
        self.contacts = []
        self.save()
        if self.tgid:
            try:
                del self.by_tgid[self.tgid]
            except KeyError:
                pass
            self.tgid = None
            self.save()
        ok = await self.client.log_out()
        if not ok:
            return False
        self.delete()
        return True

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

    async def sync_dialogs(self, synchronous_create=False):
        creators = []
        for entity in await self._get_dialogs(limit=30):
            portal = po.Portal.get_by_entity(entity)
            self.portals[portal.tgid_full] = portal
            creators.append(
                portal.create_matrix_room(self, entity, invites=[self.mxid],
                                          synchronous=synchronous_create))
        self.save()
        await asyncio.gather(*creators, loop=self.loop)

    def register_portal(self, portal):
        try:
            if self.portals[portal.tgid_full] == portal:
                return
        except KeyError:
            pass
        self.portals[portal.tgid_full] = portal
        self.save()

    def unregister_portal(self, portal):
        try:
            del self.portals[portal.tgid_full]
            self.save()
        except KeyError:
            pass

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
            return user

        if create:
            user = cls(mxid)
            cls.db.add(user.db_instance)
            cls.db.commit()
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
            return user

        return None

    @classmethod
    def find_by_username(cls, username):
        if not username:
            return None

        for _, user in cls.by_tgid.items():
            if user.username and user.username.lower() == username.lower():
                return user

        puppet = DBUser.query.filter(DBUser.tg_username == username).one_or_none()
        if puppet:
            return cls.from_db(puppet)

        return None
    # endregion


def init(context):
    global config
    config = context.config

    users = [User.from_db(user) for user in DBUser.query.all()]
    return [user.start(delete_unless_authenticated=True) for user in users]
