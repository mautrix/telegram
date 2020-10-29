# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2020 Tulir Asokan
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
from typing import (Awaitable, Dict, List, Iterable, NamedTuple, Optional, Tuple, Any, cast,
                    TYPE_CHECKING)
from collections import defaultdict
import logging
import asyncio

from telethon.tl.types import (TypeUpdate, UpdateNewMessage, UpdateNewChannelMessage, PeerUser,
                               UpdateShortChatMessage, UpdateShortMessage, User as TLUser, Chat,
                               ChatForbidden)
from telethon.tl.custom import Dialog
from telethon.tl.types.contacts import ContactsNotModified
from telethon.tl.functions.contacts import GetContactsRequest, SearchRequest
from telethon.tl.functions.account import UpdateStatusRequest

from mautrix.client import Client
from mautrix.errors import MatrixRequestError
from mautrix.types import UserID, RoomID
from mautrix.bridge import BaseUser
from mautrix.util.logging import TraceLogger
from mautrix.util.opt_prometheus import Gauge

from .types import TelegramID
from .db import User as DBUser, Portal as DBPortal
from .abstract_user import AbstractUser
from . import portal as po, puppet as pu

if TYPE_CHECKING:
    from .config import Config
    from .context import Context

config: Optional['Config'] = None

SearchResult = NamedTuple('SearchResult', puppet='pu.Puppet', similarity=int)

METRIC_LOGGED_IN = Gauge('bridge_logged_in', 'Users logged into bridge')
METRIC_CONNECTED = Gauge('bridge_connected', 'Users connected to Telegram')


class User(AbstractUser, BaseUser):
    log: TraceLogger = logging.getLogger("mau.user")
    by_mxid: Dict[str, 'User'] = {}
    by_tgid: Dict[int, 'User'] = {}

    phone: Optional[str]
    contacts: List['pu.Puppet']
    saved_contacts: int
    portals: Dict[Tuple[TelegramID, TelegramID], 'po.Portal']
    command_status: Optional[Dict[str, Any]]

    _db_instance: Optional[DBUser]
    _ensure_started_lock: asyncio.Lock
    _track_connection_task: Optional[asyncio.Task]

    def __init__(self, mxid: UserID, tgid: Optional[TelegramID] = None,
                 username: Optional[str] = None, phone: Optional[str] = None,
                 db_contacts: Optional[Iterable[TelegramID]] = None,
                 saved_contacts: int = 0, is_bot: bool = False,
                 db_portals: Optional[Iterable[Tuple[TelegramID, TelegramID]]] = None,
                 db_instance: Optional[DBUser] = None) -> None:
        super().__init__()
        self.mxid = mxid
        self.tgid = tgid
        self.is_bot = is_bot
        self.username = username
        self.phone = phone
        self.contacts = []
        self.saved_contacts = saved_contacts
        self.db_contacts = db_contacts
        self.portals = {}
        self.db_portals = db_portals or []
        self._db_instance = db_instance
        self._ensure_started_lock = asyncio.Lock()
        self.dm_update_lock = asyncio.Lock()
        self._metric_value = defaultdict(lambda: False)
        self._track_connection_task = None

        self.command_status = None

        (self.relaybot_whitelisted,
         self.whitelisted,
         self.puppet_whitelisted,
         self.matrix_puppet_whitelisted,
         self.is_admin,
         self.permissions) = config.get_permissions(self.mxid)

        self.by_mxid[mxid] = self
        if tgid:
            self.by_tgid[tgid] = self

        self.log = self.log.getChild(self.mxid)

    @property
    def name(self) -> str:
        return self.mxid

    @property
    def mxid_localpart(self) -> str:
        localpart, server = Client.parse_user_id(self.mxid)
        return localpart

    @property
    def human_tg_id(self) -> str:
        return f"@{self.username}" if self.username else f"+{self.phone}" or None

    # TODO replace with proper displayname getting everywhere
    @property
    def displayname(self) -> str:
        return self.mxid_localpart

    @property
    def plain_displayname(self) -> str:
        return self.displayname

    @property
    def db_contacts(self) -> Iterable[TelegramID]:
        return (puppet.id
                for puppet in self.contacts
                if puppet)

    @db_contacts.setter
    def db_contacts(self, contacts: Iterable[TelegramID]) -> None:
        self.contacts = [pu.Puppet.get(entry) for entry in contacts] if contacts else []

    @property
    def db_portals(self) -> Iterable[Tuple[TelegramID, TelegramID]]:
        return (portal.tgid_full
                for portal in self.portals.values()
                if portal and not portal.deleted)

    @db_portals.setter
    def db_portals(self, portals: Iterable[Tuple[TelegramID, TelegramID]]) -> None:
        self.portals = {
            tgid_full: po.Portal.get_by_tgid(*tgid_full)
            for tgid_full in portals
        } if portals else {}

    # region Database conversion

    @property
    def db_instance(self) -> DBUser:
        if not self._db_instance:
            self._db_instance = self.new_db_instance()
        return self._db_instance

    def new_db_instance(self) -> DBUser:
        return DBUser(mxid=self.mxid, tgid=self.tgid, tg_username=self.username,
                      saved_contacts=self.saved_contacts, portals=self.db_portals)

    async def save(self, contacts: bool = False, portals: bool = False) -> None:
        self.db_instance.edit(tgid=self.tgid, tg_username=self.username, tg_phone=self.phone,
                              saved_contacts=self.saved_contacts)
        if contacts:
            self.db_instance.contacts = self.db_contacts
        if portals:
            self.db_instance.portals = self.db_portals

    def delete(self, delete_db: bool = True) -> None:
        try:
            del self.by_mxid[self.mxid]
            del self.by_tgid[self.tgid]
        except KeyError:
            pass
        if delete_db and self._db_instance:
            self._db_instance.delete()

    @classmethod
    def from_db(cls, db_user: DBUser) -> 'User':
        return User(db_user.mxid, db_user.tgid, db_user.tg_username, db_user.tg_phone,
                    db_user.contacts, db_user.saved_contacts, False, db_user.portals,
                    db_instance=db_user)

    # endregion
    # region Telegram connection management

    async def try_ensure_started(self) -> None:
        try:
            await self.ensure_started()
        except Exception:
            self.log.exception("Exception in ensure_started")

    async def ensure_started(self, even_if_no_session=False) -> 'User':
        if not self.puppet_whitelisted or self.connected:
            return self
        async with self._ensure_started_lock:
            return cast(User, await super().ensure_started(even_if_no_session))

    async def start(self, delete_unless_authenticated: bool = False) -> 'User':
        await super().start()
        if await self.is_logged_in():
            self.log.debug(f"Ensuring post_login() for {self.name}")
            self.loop.create_task(self.post_login())
        elif delete_unless_authenticated:
            self.log.debug(f"Unauthenticated user {self.name} start()ed, deleting session...")
            await self.client.disconnect()
            self.client.session.delete()
        return self

    async def _track_connection(self) -> None:
        self.log.debug("Starting loop to track connection state")
        while True:
            await asyncio.sleep(3)
            connected = bool(self.client._sender._transport_connected
                             if self.client and self.client._sender else False)
            self._track_metric(METRIC_CONNECTED, connected)

    async def stop(self) -> None:
        await super().stop()
        if self._track_connection_task:
            self._track_connection_task.cancel()
            self._track_connection_task = None
        self._track_metric(METRIC_CONNECTED, False)

    async def post_login(self, info: TLUser = None, first_login: bool = False) -> None:
        if config["metrics.enabled"] and not self._track_connection_task:
            self._track_connection_task = self.loop.create_task(self._track_connection())

        try:
            await self.update_info(info)
        except Exception:
            self.log.exception("Failed to update telegram account info")
            return

        self._track_metric(METRIC_LOGGED_IN, True)

        try:
            puppet = pu.Puppet.get(self.tgid)
            if puppet.custom_mxid != self.mxid and puppet.can_auto_login(self.mxid):
                self.log.info(f"Automatically enabling custom puppet")
                await puppet.switch_mxid(access_token="auto", mxid=self.mxid)
        except Exception:
            self.log.exception("Failed to automatically enable custom puppet")

        if not self.is_bot and config["bridge.startup_sync"]:
            try:
                await self.sync_dialogs()
                await self.sync_contacts()
            except Exception:
                self.log.exception("Failed to run post-login sync")

    async def update(self, update: TypeUpdate) -> bool:
        if not self.is_bot:
            return False

        if isinstance(update, (UpdateNewMessage, UpdateNewChannelMessage)):
            portal = po.Portal.get_by_entity(update.message.peer_id, receiver_id=self.tgid)
        elif isinstance(update, UpdateShortChatMessage):
            portal = po.Portal.get_by_tgid(TelegramID(update.chat_id))
        elif isinstance(update, UpdateShortMessage):
            portal = po.Portal.get_by_tgid(TelegramID(update.user_id), self.tgid, "user")
        else:
            return False

        if portal:
            await self.register_portal(portal)
            return False

        # Don't bother handling the update
        return True

    # endregion
    # region Telegram actions that need custom methods

    async def set_presence(self, online: bool = True) -> None:
        if not self.is_bot:
            await self.client(UpdateStatusRequest(offline=not online))

    async def update_info(self, info: TLUser = None) -> None:
        info = info or await self.client.get_me()
        changed = False
        if self.is_bot != info.bot:
            self.is_bot = info.bot
            changed = True
        if self.username != info.username:
            self.username = info.username
            changed = True
        if self.phone != info.phone:
            self.phone = info.phone
            changed = True
        if self.tgid != info.id:
            self.tgid = TelegramID(info.id)
            self.by_tgid[self.tgid] = self
        if changed:
            await self.save()

    async def log_out(self) -> bool:
        puppet = pu.Puppet.get(self.tgid)
        if puppet.is_real_user:
            await puppet.switch_mxid(None, None)
        for _, portal in self.portals.items():
            if not portal or portal.deleted or not portal.mxid or portal.has_bot:
                continue
            if portal.peer_type == "user":
                await portal.cleanup_portal("Logged out of Telegram")
            else:
                try:
                    await portal.main_intent.kick_user(portal.mxid, self.mxid,
                                                       "Logged out of Telegram.")
                except MatrixRequestError:
                    pass
        self.portals = {}
        self.contacts = []
        await self.save(portals=True, contacts=True)
        if self.tgid:
            try:
                del self.by_tgid[self.tgid]
            except KeyError:
                pass
            self.tgid = None
            await self.save()
        ok = await self.client.log_out()
        if not ok:
            return False
        self.delete()
        await self.stop()
        self._track_metric(METRIC_LOGGED_IN, False)
        return True

    def _search_local(self, query: str, max_results: int = 5, min_similarity: int = 45
                      ) -> List[SearchResult]:
        results: List[SearchResult] = []
        for contact in self.contacts:
            similarity = contact.similarity(query)
            if similarity >= min_similarity:
                results.append(SearchResult(contact, similarity))
        results.sort(key=lambda tup: tup[1], reverse=True)
        return results[0:max_results]

    async def _search_remote(self, query: str, max_results: int = 5) -> List[SearchResult]:
        if len(query) < 5:
            return []
        server_results = await self.client(SearchRequest(q=query, limit=max_results))
        results: List[SearchResult] = []
        for user in server_results.users:
            puppet = pu.Puppet.get(user.id)
            await puppet.update_info(self, user)
            results.append(SearchResult(puppet, puppet.similarity(query)))
        results.sort(key=lambda tup: tup[1], reverse=True)
        return results[0:max_results]

    async def search(self, query: str, force_remote: bool = False
                     ) -> Tuple[List[SearchResult], bool]:
        if force_remote:
            return await self._search_remote(query), True

        results = self._search_local(query)
        if results:
            return results, False

        return await self._search_remote(query), True

    async def _catch(self, action: str, task: asyncio.Task) -> None:
        try:
            await task
        except Exception:
            self.log.exception(f"Error while {action}")

    async def get_direct_chats(self) -> Dict[UserID, List[RoomID]]:
        return {
            pu.Puppet.get_mxid_from_id(portal.tgid): [portal.mxid]
            for portal in DBPortal.find_private_chats(self.tgid)
            if portal.mxid
        }

    async def sync_dialogs(self) -> None:
        if self.is_bot:
            return
        creators = []
        update_limit = config["bridge.sync_update_limit"] or None
        create_limit = config["bridge.sync_create_limit"]
        index = 0
        self.log.debug(f"Syncing dialogs (update_limit={update_limit}, "
                       f"create_limit={create_limit})")
        dialog: Dialog
        async for dialog in self.client.iter_dialogs(limit=update_limit, ignore_migrated=True,
                                                     archived=False):
            entity = dialog.entity
            if isinstance(entity, ChatForbidden):
                self.log.warning(f"Ignoring forbidden chat {entity} while syncing")
                continue
            elif isinstance(entity, Chat) and (entity.deactivated or entity.left):
                self.log.warning(f"Ignoring deactivated or left chat {entity} while syncing")
                continue
            elif isinstance(entity, TLUser) and not config["bridge.sync_direct_chats"]:
                self.log.trace(f"Ignoring user {entity.id} while syncing")
                continue
            portal = po.Portal.get_by_entity(entity, receiver_id=self.tgid)
            self.portals[portal.tgid_full] = portal
            if portal.mxid:
                update_task = portal.update_matrix_room(self, entity)
                backfill_task = portal.backfill(self, last_id=dialog.message.id)
                creators.append(self._catch(f"updating {portal.tgid_log}",
                                            self.loop.create_task(update_task)))
                creators.append(self._catch(f"backfilling {portal.tgid_log}",
                                            self.loop.create_task(backfill_task)))
            elif not create_limit or index < create_limit:
                create_task = portal.create_matrix_room(self, entity, invites=[self.mxid])
                creators.append(self._catch(f"creating {portal.tgid_log}",
                                            self.loop.create_task(create_task)))
            index += 1
        await self.save(portals=True)
        await asyncio.gather(*creators)
        await self.update_direct_chats()
        self.log.debug("Dialog syncing complete")

    async def register_portal(self, portal: po.Portal) -> None:
        self.log.trace(f"Registering portal {portal.tgid_full}")
        try:
            if self.portals[portal.tgid_full] == portal:
                return
        except KeyError:
            pass
        self.portals[portal.tgid_full] = portal
        await self.save(portals=True)

    async def unregister_portal(self, tgid: TelegramID, tg_receiver: TelegramID) -> None:
        self.log.trace(f"Unregistering portal {(tgid, tg_receiver)}")
        try:
            del self.portals[(tgid, tg_receiver)]
            await self.save(portals=True)
        except KeyError:
            pass

    async def needs_relaybot(self, portal: po.Portal) -> bool:
        return not await self.is_logged_in() or (
            (portal.has_bot or self.is_bot) and portal.tgid_full not in self.portals)

    def _hash_contacts(self) -> int:
        acc = 0
        for contact in sorted([self.saved_contacts] + [contact.id for contact in self.contacts]):
            acc = (acc * 20261 + contact) & 0xffffffff
        return acc & 0x7fffffff

    async def sync_contacts(self) -> None:
        response = await self.client(GetContactsRequest(hash=self._hash_contacts()))
        if isinstance(response, ContactsNotModified):
            return
        self.log.debug(f"Updating contacts of {self.name}...")
        self.contacts = []
        self.saved_contacts = response.saved_count
        for user in response.users:
            puppet = pu.Puppet.get(user.id)
            await puppet.update_info(self, user)
            self.contacts.append(puppet)
        await self.save(contacts=True)

    # endregion
    # region Class instance lookup

    @classmethod
    def get_by_mxid(cls, mxid: UserID, create: bool = True, check_db: bool = True
                    ) -> Optional['User']:
        if not mxid:
            raise ValueError("Matrix ID can't be empty")

        try:
            return cls.by_mxid[mxid]
        except KeyError:
            pass

        if check_db:
            user = DBUser.get_by_mxid(mxid)
            if user:
                user = cls.from_db(user)
                return user

        if create:
            user = cls(mxid)
            user.db_instance.insert()
            return user

        return None

    @classmethod
    def get_by_tgid(cls, tgid: TelegramID) -> Optional['User']:
        try:
            return cls.by_tgid[tgid]
        except KeyError:
            pass

        user = DBUser.get_by_tgid(tgid)
        if user:
            user = cls.from_db(user)
            return user

        return None

    @classmethod
    def find_by_username(cls, username: str) -> Optional['User']:
        if not username:
            return None

        username = username.lower()

        for _, user in cls.by_tgid.items():
            if user.username and user.username.lower() == username:
                return user

        puppet = DBUser.get_by_username(username)
        if puppet:
            return cls.from_db(puppet)

        return None
    # endregion


def init(context: 'Context') -> Iterable[Awaitable['User']]:
    global config
    config = context.config
    User.bridge = context.bridge

    return (User.from_db(db_user).try_ensure_started()
            for db_user in DBUser.all_with_tgid())
