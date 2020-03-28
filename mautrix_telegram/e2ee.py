# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2019 Tulir Asokan
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
from typing import Tuple, Union
import logging
import asyncio
import hashlib
import hmac

from nio import AsyncClient, Event as NioEvent, GroupEncryptionError, LoginError

from mautrix.appservice import AppService
from mautrix.types import (Filter, RoomFilter, EventFilter, RoomEventFilter, StateFilter,
                           EventType, RoomID, Serializable, JSON, MessageEvent, Event)

from .context import Context


class EncryptionManager:
    loop: asyncio.AbstractEventLoop
    log: logging.Logger = logging.getLogger("mau.e2ee")
    client: AsyncClient
    az: AppService

    login_shared_secret: bytes

    sync_task: asyncio.Task

    def __init__(self, context: 'Context') -> None:
        self.loop = context.loop
        self.az = context.az
        self.config = context.config
        lss: str = self.config["bridge.login_shared_secret"]
        if not lss:
            raise ValueError("login_shared_secret must be set to enable encryption")
        self.login_shared_secret = lss.encode("utf-8")
        self.client = AsyncClient(homeserver=self.config["homeserver.address"],
                                  user=self.az.bot_mxid, device_id="Telegram bridge",
                                  store_path="nio_store")

    async def encrypt(self, room_id: RoomID, event_type: EventType,
                      content: Union[Serializable, JSON]) -> Tuple[EventType, JSON]:
        serialized = content.serialize() if isinstance(content, Serializable) else content
        type_str = str(event_type)
        retries = 0
        while True:
            try:
                type_str, encrypted = self.client.encrypt(room_id, type_str, serialized)
                break
            except GroupEncryptionError:
                if retries > 3:
                    self.log.error("Got GroupEncryptionError again, giving up")
                    raise
                retries += 1
                self.log.debug("Got GroupEncryptionError, sharing group session and trying again")
                await self.client.share_group_session(room_id, ignore_unverified_devices=True)
        event_type = EventType.find(type_str)
        try:
            encrypted["m.relates_to"] = serialized["m.relates_to"]
        except KeyError:
            pass
        return event_type, encrypted

    def decrypt(self, event: MessageEvent) -> MessageEvent:
        serialized = event.serialize()
        event = self.client.decrypt_event(NioEvent.parse_encrypted_event(serialized))
        try:
            event.source["content"]["m.relates_to"] = serialized["content"]["m.relates_to"]
        except KeyError:
            pass
        return Event.deserialize(event.source)

    async def start(self) -> None:
        self.log.debug("Logging in with bridge bot user")
        password = hmac.new(self.login_shared_secret, self.az.bot_mxid.encode("utf-8"),
                            hashlib.sha512).hexdigest()
        resp = await self.client.login(password, device_name="Telegram bridge")
        if isinstance(resp, LoginError):
            raise resp
        self.sync_task = self.loop.create_task(self.client.sync_forever(
            timeout=30000, sync_filter=self._filter.serialize()))
        self.log.info("End-to-bridge encryption support is enabled")

    def stop(self) -> None:
        self.sync_task.cancel()

    @property
    def _filter(self) -> Filter:
        all_events = EventType.find("*")
        return Filter(
            account_data=EventFilter(types=[all_events]),
            presence=EventFilter(not_types=[all_events]),
            room=RoomFilter(
                include_leave=False,
                state=StateFilter(types=[EventType.ROOM_MEMBER, EventType.ROOM_ENCRYPTION]),
                timeline=RoomEventFilter(types=[EventType.ROOM_MEMBER, EventType.ROOM_ENCRYPTION]),
                account_data=RoomEventFilter(not_types=[all_events]),
                ephemeral=RoomEventFilter(not_types=[all_events]),
            ),
        )
