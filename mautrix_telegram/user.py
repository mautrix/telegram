# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2021 Tulir Asokan
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
from __future__ import annotations

from typing import TYPE_CHECKING, AsyncGenerator, AsyncIterable, Awaitable, NamedTuple, cast
from datetime import datetime, timezone
import asyncio

from telethon.errors import AuthKeyDuplicatedError, RPCError, UnauthorizedError
from telethon.tl.custom import Dialog
from telethon.tl.functions.account import UpdateStatusRequest
from telethon.tl.functions.contacts import GetContactsRequest, SearchRequest
from telethon.tl.functions.updates import GetStateRequest
from telethon.tl.functions.users import GetUsersRequest
from telethon.tl.types import (
    Channel,
    Chat,
    ChatForbidden,
    InputUserSelf,
    NotifyPeer,
    PeerUser,
    TypeUpdate,
    UpdateFolderPeers,
    UpdateNewChannelMessage,
    UpdateNewMessage,
    UpdateNotifySettings,
    UpdatePinnedDialogs,
    UpdateShortChatMessage,
    UpdateShortMessage,
    User as TLUser,
)
from telethon.tl.types.contacts import ContactsNotModified

from mautrix.appservice import DOUBLE_PUPPET_SOURCE_KEY
from mautrix.bridge import BaseUser, async_getter_lock
from mautrix.client import Client
from mautrix.errors import MatrixRequestError, MNotFound
from mautrix.types import PushActionType, PushRuleKind, PushRuleScope, RoomID, RoomTagInfo, UserID
from mautrix.util.bridge_state import BridgeState, BridgeStateEvent
from mautrix.util.opt_prometheus import Gauge

from . import portal as po, puppet as pu
from .abstract_user import AbstractUser
from .db import Message as DBMessage, PgSession, User as DBUser
from .types import TelegramID

if TYPE_CHECKING:
    from .__main__ import TelegramBridge

SearchResult = NamedTuple("SearchResult", puppet="pu.Puppet", similarity=int)

METRIC_LOGGED_IN = Gauge("bridge_logged_in", "Users logged into bridge")
METRIC_CONNECTED = Gauge("bridge_connected", "Users connected to Telegram")

BridgeState.human_readable_errors.update(
    {
        "tg-not-connected": "Your Telegram connection failed",
        "tg-auth-key-duplicated": "The bridge accidentally logged you out",
        "tg-not-authenticated": "The stored auth token did not work",
        "tg-no-auth": "You're not logged in",
    }
)


class User(DBUser, AbstractUser, BaseUser):
    by_mxid: dict[str, User] = {}
    by_tgid: dict[int, User] = {}

    _portals_cache: dict[tuple[TelegramID, TelegramID], po.Portal] | None

    _ensure_started_lock: asyncio.Lock
    _track_connection_task: asyncio.Task | None
    _is_backfilling: bool

    def __init__(
        self,
        mxid: UserID,
        tgid: TelegramID | None = None,
        tg_username: str | None = None,
        tg_phone: str | None = None,
        is_bot: bool = False,
        is_premium: bool = False,
        saved_contacts: int = 0,
    ) -> None:
        super().__init__(
            mxid=mxid,
            tgid=tgid,
            tg_username=tg_username,
            tg_phone=tg_phone,
            is_bot=is_bot,
            is_premium=is_premium,
            saved_contacts=saved_contacts,
        )
        AbstractUser.__init__(self)
        BaseUser.__init__(self)
        self._ensure_started_lock = asyncio.Lock()
        self._track_connection_task = None
        self._is_backfilling = False
        self._portals_cache = None

        (
            self.relaybot_whitelisted,
            self.whitelisted,
            self.puppet_whitelisted,
            self.matrix_puppet_whitelisted,
            self.is_admin,
            self.permissions,
        ) = self.config.get_permissions(self.mxid)

    @property
    def name(self) -> str:
        return self.mxid

    @property
    def mxid_localpart(self) -> str:
        localpart, server = Client.parse_user_id(self.mxid)
        return localpart

    @property
    def human_tg_id(self) -> str:
        return f"@{self.tg_username}" if self.tg_username else f"+{self.tg_phone}" or None

    @property
    def peer(self) -> PeerUser | None:
        return PeerUser(user_id=self.tgid) if self.tgid else None

    # TODO replace with proper displayname getting everywhere
    @property
    def displayname(self) -> str:
        return self.mxid_localpart

    @property
    def plain_displayname(self) -> str:
        return self.displayname

    @classmethod
    def init_cls(cls, bridge: "TelegramBridge") -> AsyncIterable[Awaitable[User]]:
        cls.config = bridge.config
        cls.bridge = bridge
        cls.az = bridge.az
        cls.loop = bridge.loop

        return (user.try_ensure_started() async for user in cls.all_with_tgid())

    # region Telegram connection management

    async def try_ensure_started(self) -> None:
        try:
            await self.ensure_started()
        except Exception:
            self.log.exception("Exception in ensure_started")
        else:
            if not self.client and not await PgSession.has(self.mxid):
                self.log.warning("Didn't start user: no session stored")
                if self.tgid:
                    await self.push_bridge_state(
                        BridgeStateEvent.BAD_CREDENTIALS, error="tg-no-auth"
                    )

    async def ensure_started(self, even_if_no_session=False) -> User:
        if not self.puppet_whitelisted or self.connected:
            return self
        async with self._ensure_started_lock:
            return cast(User, await super().ensure_started(even_if_no_session))

    async def start(self, delete_unless_authenticated: bool = False) -> User:
        try:
            await super().start()
        except AuthKeyDuplicatedError:
            self.log.warning("Got AuthKeyDuplicatedError in start()")
            await self.push_bridge_state(
                BridgeStateEvent.BAD_CREDENTIALS, error="tg-auth-key-duplicated"
            )
            await self.client.disconnect()
            await self.client.session.delete()
            self.client = None
            if not delete_unless_authenticated:
                # The caller wants the client to be connected, so restart the connection.
                await super().start()
            return self
        except Exception:
            await self.push_bridge_state(BridgeStateEvent.UNKNOWN_ERROR)
            raise
        try:
            assert self.client, "client is undefined"
            assert self.client.is_connected(), "client is not connected"
            await self.client(GetStateRequest())
        except AssertionError as e:
            self.log.error(f"Client in bad state after start(): {e}")
            if self.tgid:
                await self.push_bridge_state(BridgeStateEvent.UNKNOWN_ERROR, message=str(e))
        except UnauthorizedError as e:
            if delete_unless_authenticated or self.tgid:
                self.log.error(f"Authorization error in start(): {type(e)}: {e}")
            if self.tgid:
                await self.push_bridge_state(
                    BridgeStateEvent.BAD_CREDENTIALS,
                    error="tg-auth-error",
                    message=str(e),
                    ttl=3600,
                )
        except RPCError as e:
            self.log.error(f"Unknown RPC error in start(): {type(e)}: {e}")
            if self.tgid:
                await self.push_bridge_state(BridgeStateEvent.UNKNOWN_ERROR, message=str(e))
        else:
            # Authenticated, run post login
            self.log.debug(f"Ensuring post_login() for {self.name}")
            asyncio.create_task(self.post_login())
            return self
        # Not authenticated, delete data if necessary
        if delete_unless_authenticated:
            self.log.debug(f"Unauthenticated user {self.name} start()ed, deleting session...")
            await self.client.disconnect()
            await self.client.session.delete()
        return self

    @property
    def _is_connected(self) -> bool:
        return bool(
            self.client and self.client._sender and self.client._sender._transport_connected()
        )

    async def _track_connection(self) -> None:
        self.log.debug("Starting loop to track connection state")
        while True:
            await asyncio.sleep(3)
            connected = self._is_connected
            self._track_metric(METRIC_CONNECTED, connected)
            if connected:
                await self.push_bridge_state(
                    BridgeStateEvent.BACKFILLING
                    if self._is_backfilling
                    else BridgeStateEvent.CONNECTED,
                    ttl=3600,
                )
            else:
                await self.push_bridge_state(
                    BridgeStateEvent.TRANSIENT_DISCONNECT, ttl=240, error="tg-not-connected"
                )

    async def fill_bridge_state(self, state: BridgeState) -> None:
        await super().fill_bridge_state(state)
        state.remote_id = str(self.tgid)
        state.remote_name = self.human_tg_id

    async def get_bridge_states(self) -> list[BridgeState]:
        if not self.tgid:
            return []
        if self._is_connected and await self.is_logged_in():
            state_event = (
                BridgeStateEvent.BACKFILLING
                if self._is_backfilling
                else BridgeStateEvent.CONNECTED
            )
            ttl = 3600
        else:
            state_event = BridgeStateEvent.UNKNOWN_ERROR
            ttl = 240
        return [BridgeState(state_event=state_event, ttl=ttl)]

    async def get_puppet(self) -> pu.Puppet | None:
        if not self.tgid:
            return None
        return await pu.Puppet.get_by_tgid(self.tgid)

    async def get_portal_with(self, puppet: pu.Puppet, create: bool = True) -> po.Portal | None:
        if not self.tgid:
            return None
        return await po.Portal.get_by_tgid(
            puppet.tgid, tg_receiver=self.tgid, peer_type="user" if create else None
        )

    async def stop(self) -> None:
        if self._track_connection_task:
            self._track_connection_task.cancel()
            self._track_connection_task = None
        await super().stop()
        self._track_metric(METRIC_CONNECTED, False)

    async def post_login(self, info: TLUser = None, first_login: bool = False) -> None:
        if (
            self.config["metrics.enabled"] or self.config["homeserver.status_endpoint"]
        ) and not self._track_connection_task:
            self._track_connection_task = asyncio.create_task(self._track_connection())

        try:
            await self.update_info(info)
        except Exception:
            self.log.exception("Failed to update telegram account info")
            return

        self._track_metric(METRIC_LOGGED_IN, True)

        try:
            puppet = await pu.Puppet.get_by_tgid(self.tgid)
            if puppet.custom_mxid != self.mxid and puppet.can_auto_login(self.mxid):
                self.log.info(f"Automatically enabling custom puppet")
                await puppet.switch_mxid(access_token="auto", mxid=self.mxid)
        except Exception:
            self.log.exception("Failed to automatically enable custom puppet")

        if not self.is_bot and self.config["bridge.startup_sync"]:
            try:
                self._is_backfilling = True
                await self.sync_dialogs()
                await self.sync_contacts()
            except Exception:
                self.log.exception("Failed to run post-login sync")
            finally:
                self._is_backfilling = False

    async def update(self, update: TypeUpdate) -> bool:
        if not self.is_bot:
            return False

        if isinstance(update, (UpdateNewMessage, UpdateNewChannelMessage)):
            portal = await po.Portal.get_by_entity(update.message.peer_id, tg_receiver=self.tgid)
        elif isinstance(update, UpdateShortChatMessage):
            portal = await po.Portal.get_by_tgid(TelegramID(update.chat_id))
        elif isinstance(update, UpdateShortMessage):
            portal = await po.Portal.get_by_tgid(
                TelegramID(update.user_id), tg_receiver=self.tgid, peer_type="user"
            )
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

    async def get_me(self) -> TLUser | None:
        try:
            return (await self.client(GetUsersRequest([InputUserSelf()])))[0]
        except UnauthorizedError as e:
            self.log.error(f"Authorization error in get_me(): {type(e)}: {e}")
            await self.push_bridge_state(
                BridgeStateEvent.BAD_CREDENTIALS, error="tg-auth-error", message=str(e), ttl=3600
            )
            await self.stop()
            return None

    async def update_info(self, info: TLUser = None) -> None:
        if not info:
            info = await self.get_me()
            if not info:
                self.log.warning("get_me() returned None, aborting update_info()")
                return
        changed = False
        if self.is_bot != info.bot:
            self.is_bot = info.bot
            changed = True
        if self.is_premium != info.premium:
            self.is_premium = info.premium
            changed = True
        if self.tg_username != info.username:
            self.tg_username = info.username
            changed = True
        if self.tg_phone != info.phone:
            self.tg_phone = info.phone
            changed = True
        if self.tgid != info.id:
            self.tgid = TelegramID(info.id)
            self.by_tgid[self.tgid] = self
        if changed:
            await self.save()

    async def kick_from_portals(self) -> None:
        if not self.config["bridge.kick_on_logout"]:
            return
        portals = await self.get_cached_portals()
        for portal in portals.values():
            if not portal or portal.deleted or not portal.mxid or portal.has_bot:
                continue
            if portal.peer_type == "user":
                await portal.cleanup_portal("Logged out of Telegram")
            else:
                try:
                    await portal.main_intent.kick_user(
                        portal.mxid, self.mxid, "Logged out of Telegram."
                    )
                except MatrixRequestError:
                    pass

    async def log_out(self) -> bool:
        puppet = await pu.Puppet.get_by_tgid(self.tgid)
        if puppet.is_real_user:
            await puppet.switch_mxid(None, None)
        try:
            await self.kick_from_portals()
        except Exception:
            self.log.exception("Failed to kick user from portals on logout")
        await self.push_bridge_state(BridgeStateEvent.LOGGED_OUT)
        if self.tgid:
            try:
                del self.by_tgid[self.tgid]
            except KeyError:
                pass
            self.tgid = None
        ok = await self.client.log_out()
        sess = self.client.session
        await self.stop()
        await sess.delete()
        await self.delete()
        self.by_mxid.pop(self.mxid, None)
        self._track_metric(METRIC_LOGGED_IN, False)
        return ok

    async def _search_local(
        self, query: str, max_results: int = 5, min_similarity: int = 45
    ) -> list[SearchResult]:
        results: list[SearchResult] = []
        for contact_id in await self.get_contacts():
            contact = await pu.Puppet.get_by_tgid(contact_id, create=False)
            if not contact:
                continue
            similarity = contact.similarity(query)
            if similarity >= min_similarity:
                results.append(SearchResult(contact, similarity))
        results.sort(key=lambda tup: tup[1], reverse=True)
        return results[0:max_results]

    async def _search_remote(self, query: str, max_results: int = 5) -> list[SearchResult]:
        if len(query) < 5:
            return []
        server_results = await self.client(SearchRequest(q=query, limit=max_results))
        results: list[SearchResult] = []
        for user in server_results.users:
            puppet = await pu.Puppet.get_by_tgid(user.id)
            await puppet.update_info(self, user)
            results.append(SearchResult(puppet, puppet.similarity(query)))
        results.sort(key=lambda tup: tup[1], reverse=True)
        return results[0:max_results]

    async def search(
        self, query: str, force_remote: bool = False
    ) -> tuple[list[SearchResult], bool]:
        if force_remote:
            return await self._search_remote(query), True

        results = await self._search_local(query)
        if results:
            return results, False

        return await self._search_remote(query), True

    async def get_direct_chats(self) -> dict[UserID, list[RoomID]]:
        return {
            pu.Puppet.get_mxid_from_id(portal.tgid): [portal.mxid]
            async for portal in po.Portal.find_private_chats_of(self.tgid)
            if portal.mxid
        }

    async def _tag_room(
        self, puppet: pu.Puppet, portal: po.Portal, tag: str, active: bool
    ) -> None:
        if not tag or not portal or not portal.mxid:
            return
        tag_info = await puppet.intent.get_room_tag(portal.mxid, tag)
        if active and tag_info is None:
            tag_info = RoomTagInfo(order=0.5)
            tag_info[DOUBLE_PUPPET_SOURCE_KEY] = self.bridge.name
            self.log.debug("Adding tag {tag} to {portal.mxid}/{portal.tgid}")
            await puppet.intent.set_room_tag(portal.mxid, tag, tag_info)
        elif (
            not active and tag_info and tag_info.get(DOUBLE_PUPPET_SOURCE_KEY) == self.bridge.name
        ):
            self.log.debug("Removing tag {tag} from {portal.mxid}/{portal.tgid}")
            await puppet.intent.remove_room_tag(portal.mxid, tag)

    async def _mute_room(self, puppet: pu.Puppet, portal: po.Portal, mute_until: datetime) -> None:
        if not self.config["bridge.mute_bridging"] or not portal or not portal.mxid:
            return
        now = datetime.utcnow().replace(tzinfo=timezone.utc)
        if mute_until is not None and mute_until > now:
            self.log.debug(
                f"Muting {portal.mxid}/{portal.tgid} (muted until {mute_until} on Telegram)"
            )
            await puppet.intent.set_push_rule(
                PushRuleScope.GLOBAL,
                PushRuleKind.ROOM,
                portal.mxid,
                actions=[PushActionType.DONT_NOTIFY],
            )
        else:
            try:
                await puppet.intent.remove_push_rule(
                    PushRuleScope.GLOBAL, PushRuleKind.ROOM, portal.mxid
                )
                self.log.debug(f"Unmuted {portal.mxid}/{portal.tgid}")
            except MNotFound:
                pass

    async def update_folder_peers(self, update: UpdateFolderPeers) -> None:
        if self.config["bridge.tag_only_on_create"]:
            return
        puppet = await pu.Puppet.get_by_custom_mxid(self.mxid)
        if not puppet or not puppet.is_real_user:
            return
        for peer in update.folder_peers:
            portal = await po.Portal.get_by_entity(peer.peer, tg_receiver=self.tgid, create=False)
            await self._tag_room(
                puppet, portal, self.config["bridge.archive_tag"], peer.folder_id == 1
            )

    async def update_pinned_dialogs(self, update: UpdatePinnedDialogs) -> None:
        if self.config["bridge.tag_only_on_create"]:
            return
        puppet = await pu.Puppet.get_by_custom_mxid(self.mxid)
        if not puppet or not puppet.is_real_user:
            return
        # TODO bridge unpinning properly
        for pinned in update.order:
            portal = await po.Portal.get_by_entity(
                pinned.peer, tg_receiver=self.tgid, create=False
            )
            await self._tag_room(puppet, portal, self.config["bridge.pinned_tag"], True)

    async def update_notify_settings(self, update: UpdateNotifySettings) -> None:
        if self.config["bridge.tag_only_on_create"]:
            return
        elif not isinstance(update.peer, NotifyPeer):
            # TODO handle global notification setting changes?
            return
        puppet = await pu.Puppet.get_by_custom_mxid(self.mxid)
        if not puppet or not puppet.is_real_user:
            return
        portal = await po.Portal.get_by_entity(
            update.peer.peer, tg_receiver=self.tgid, create=False
        )
        await self._mute_room(puppet, portal, update.notify_settings.mute_until)

    async def _sync_dialog(
        self, portal: po.Portal, dialog: Dialog, should_create: bool, puppet: pu.Puppet | None
    ) -> None:
        was_created = False
        if portal.mxid:
            try:
                await portal.backfill(self, last_id=dialog.message.id)
            except Exception:
                self.log.exception(f"Error while backfilling {portal.tgid_log}")
            try:
                await portal.update_matrix_room(self, dialog.entity)
            except Exception:
                self.log.exception(f"Error while updating {portal.tgid_log}")
        elif should_create:
            try:
                await portal.create_matrix_room(self, dialog.entity, invites=[self.mxid])
                was_created = True
            except Exception:
                self.log.exception(f"Error while creating {portal.tgid_log}")
        if portal.mxid and puppet and puppet.is_real_user:
            tg_space = portal.tgid if portal.peer_type == "channel" else self.tgid
            if dialog.unread_count == 0:
                # This is usually more reliable than finding a specific message
                # e.g. if the last read message is a service message that isn't in the message db
                last_read = await DBMessage.find_last(portal.mxid, tg_space)
            else:
                last_read = await DBMessage.get_one_by_tgid(
                    portal.tgid, tg_space, dialog.dialog.read_inbox_max_id
                )
            try:
                if last_read:
                    await puppet.intent.mark_read(last_read.mx_room, last_read.mxid)
                if was_created or not self.config["bridge.tag_only_on_create"]:
                    await self._mute_room(puppet, portal, dialog.dialog.notify_settings.mute_until)
                    await self._tag_room(
                        puppet, portal, self.config["bridge.pinned_tag"], dialog.pinned
                    )
                    await self._tag_room(
                        puppet, portal, self.config["bridge.archive_tag"], dialog.archived
                    )
            except Exception:
                self.log.exception(f"Error updating read status and tags for {portal.tgid_log}")

    async def get_cached_portals(self) -> dict[tuple[TelegramID, TelegramID], po.Portal]:
        if self._portals_cache is None:
            self._portals_cache = {
                (tgid, tg_receiver): await po.Portal.get_by_tgid(tgid, tg_receiver=tg_receiver)
                for tgid, tg_receiver in await self.get_portals()
            }
        return self._portals_cache

    async def sync_dialogs(self) -> None:
        if self.is_bot:
            return
        creators = []
        update_limit = self.config["bridge.sync_update_limit"] or None
        create_limit = self.config["bridge.sync_create_limit"]
        index = 0
        self.log.debug(
            f"Syncing dialogs (update_limit={update_limit}, create_limit={create_limit})"
        )
        await self.push_bridge_state(BridgeStateEvent.BACKFILLING)
        puppet = await pu.Puppet.get_by_custom_mxid(self.mxid)
        dialog: Dialog
        old_portal_cache = await self.get_cached_portals()
        new_portal_cache = old_portal_cache.copy()
        async for dialog in self.client.iter_dialogs(
            limit=update_limit, ignore_migrated=True, archived=False
        ):
            entity = dialog.entity
            if isinstance(entity, ChatForbidden):
                self.log.warning(f"Ignoring forbidden chat {entity} while syncing")
                continue
            elif isinstance(entity, Chat) and (entity.deactivated or entity.left):
                self.log.warning(f"Ignoring deactivated or left chat {entity} while syncing")
                continue
            elif isinstance(entity, TLUser) and not self.config["bridge.sync_direct_chats"]:
                self.log.trace(f"Ignoring user {entity.id} while syncing")
                continue
            portal = await po.Portal.get_by_entity(entity, tg_receiver=self.tgid)
            new_portal_cache[portal.tgid_full] = portal
            coro = self._sync_dialog(
                portal=portal,
                dialog=dialog,
                puppet=puppet,
                should_create=not create_limit or index < create_limit,
            )
            creators.append(asyncio.create_task(coro))
            index += 1
        if new_portal_cache.keys() != old_portal_cache.keys():
            await self.set_portals(new_portal_cache.keys())
            self._portals_cache = new_portal_cache
        await asyncio.gather(*creators)
        await self.update_direct_chats()
        self.log.debug("Dialog syncing complete")

    async def register_portal(self, portal: po.Portal) -> None:
        self.log.trace(f"Registering portal {portal.tgid_full}")
        if self._portals_cache is not None:
            if self._portals_cache.get(portal.tgid_full) == portal:
                return
            self._portals_cache[portal.tgid_full] = portal
        await super().register_portal(portal.tgid, portal.tg_receiver)

    async def unregister_portal(self, tgid: TelegramID, tg_receiver: TelegramID) -> None:
        self.log.trace(f"Unregistering portal {(tgid, tg_receiver)}")
        if self._portals_cache is not None:
            self._portals_cache.pop((tgid, tg_receiver), None)
        await super().unregister_portal(tgid, tg_receiver)

    async def needs_relaybot(self, portal: po.Portal) -> bool:
        return not await self.is_logged_in() or (
            (portal.has_bot or self.is_bot)
            and portal.tgid_full not in await self.get_cached_portals()
        )

    @staticmethod
    def _hash_contacts(count: int, ids: list[TelegramID]) -> int:
        acc = 0
        for contact in sorted([count] + ids):
            acc = (acc * 20261 + contact) & 0xFFFFFFFF
        return acc & 0x7FFFFFFF

    async def sync_contacts(self, get_info: bool = False) -> dict[TelegramID, dict]:
        existing_contacts = await self.get_contacts()
        contact_hash = self._hash_contacts(self.saved_contacts, existing_contacts)
        response = await self.client(GetContactsRequest(hash=contact_hash))
        if isinstance(response, ContactsNotModified):
            if get_info:
                return {
                    tgid: (await pu.Puppet.get_by_tgid(tgid)).contact_info
                    for tgid in existing_contacts
                }
            return {}
        self.log.debug(f"Updating contacts of {self.name}...")
        if self.saved_contacts != response.saved_count:
            self.saved_contacts = response.saved_count
            await self.save()
        contacts = {}
        for user in response.users:
            puppet: pu.Puppet = await pu.Puppet.get_by_tgid(user.id)
            await puppet.update_info(self, user)
            contacts[user.id] = puppet.contact_info
        await self.set_contacts(contacts.keys())
        self.log.debug("Contact syncing complete")
        return contacts

    # endregion
    # region Class instance lookup

    def _add_to_cache(self) -> None:
        self.by_mxid[self.mxid] = self
        if self.tgid:
            self.by_tgid[self.tgid] = self

    @classmethod
    async def get_and_start_by_mxid(cls, mxid: UserID, even_if_no_session: bool = False) -> User:
        user = await cls.get_by_mxid(mxid, create=True)
        await user.ensure_started(even_if_no_session=even_if_no_session)
        return user

    @classmethod
    async def all_with_tgid(cls) -> AsyncGenerator[User, None]:
        users = await super().all_with_tgid()
        user: cls
        for user in users:
            try:
                yield cls.by_mxid[user.mxid]
            except KeyError:
                user._add_to_cache()
                yield user

    @classmethod
    @async_getter_lock
    async def get_by_mxid(
        cls, mxid: UserID, /, *, check_db: bool = True, create: bool = True
    ) -> User | None:
        if not mxid or pu.Puppet.get_id_from_mxid(mxid) or mxid == cls.az.bot_mxid:
            return None
        try:
            return cls.by_mxid[mxid]
        except KeyError:
            pass

        if not check_db:
            return None

        user = cast(cls, await super().get_by_mxid(mxid))
        if user is not None:
            user._add_to_cache()
            return user

        if create:
            cls.log.debug(f"Creating user instance for {mxid}")
            user = cls(mxid)
            await user.insert()
            user._add_to_cache()
            return user

        return None

    @classmethod
    @async_getter_lock
    async def get_by_tgid(cls, tgid: TelegramID, /) -> User | None:
        try:
            return cls.by_tgid[tgid]
        except KeyError:
            pass

        user = cast(cls, await super().get_by_tgid(tgid))
        if user is not None:
            user._add_to_cache()
            return user

        return None

    @classmethod
    async def find_by_username(cls, username: str) -> User | None:
        if not username:
            return None

        username = username.lower()

        for _, user in cls.by_tgid.items():
            if user.tg_username and user.tg_username.lower() == username:
                return user

        user = cast(cls, await super().find_by_username(username))
        if user:
            try:
                return cls.by_mxid[user.mxid]
            except KeyError:
                user._add_to_cache()
                return user

        return None

    # endregion
