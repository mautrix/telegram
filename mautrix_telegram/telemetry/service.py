# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2021 Tulir Asokan
# Copyright (C) 2022 New Vector Ltd
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

from typing import TYPE_CHECKING
import asyncio
import logging
import time

from aiohttp import ClientSession
from attr import dataclass

from mautrix.api import HTTPAPI, Method
from mautrix.client import ClientAPI
from mautrix.errors import MNotFound
from mautrix.types import EventType, RoomID, SerializableAttrs, SerializerError

from .types import TELEMETRY_TYPE, TelemetryData, TelemetryDataRMAU, TelemetryEvent

if TYPE_CHECKING:
    from ..__main__ import TelegramBridge

TELEMETRY_BASE_TYPE_NAME = f"{TELEMETRY_TYPE}.telemetry"
TELEMETRY_ROOM_TYPE_NAME = f"{TELEMETRY_BASE_TYPE_NAME}.storage.room"
TELEMETRY_EVENT_TYPE_NAME = f"{TELEMETRY_BASE_TYPE_NAME}.activity"

TelemetryEventType = EventType.find(TELEMETRY_EVENT_TYPE_NAME, EventType.Class.MESSAGE)


@dataclass
class TelemetryRoomAccountDataEventContent(SerializableAttrs):
    room_id: RoomID


class TelemetryService:
    log: logging.Logger = logging.getLogger("mau.telemetry")

    _instance_id: str
    _hostname: str
    _matrix_client: ClientAPI

    _endpoint: str
    _retry_count: int
    _retry_interval: int

    _telemetry_room_id: RoomID | None = None
    _session: ClientSession | None = None

    def __init__(self, bridge: TelegramBridge, instance_id: str) -> None:
        self._instance_id = instance_id
        self._hostname = bridge.az.domain
        self._matrix_client = bridge.az.intent

        self._endpoint = bridge.config["telemetry.endpoint"]
        self._retry_count = bridge.config["telemetry.retry_count"]
        self._retry_interval = bridge.config["telemetry.retry_interval"]
        if self._endpoint:
            self._session = ClientSession(
                loop=bridge.loop,
                headers={
                    "User-Agent": HTTPAPI.default_ua,
                    "Content-Type": "application/json",
                },
            )

    async def _load_storage_room(self) -> RoomID:
        if self._telemetry_room_id:
            return self._telemetry_room_id

        try:
            account_data = TelemetryRoomAccountDataEventContent.deserialize(
                await self._matrix_client.get_account_data(TELEMETRY_ROOM_TYPE_NAME)
            )
            room_id = account_data.room_id
        except (MNotFound, SerializerError):
            room_id = await self._matrix_client.create_room(
                creation_content={"type": TELEMETRY_ROOM_TYPE_NAME}
            )
            await self._matrix_client.set_account_data(
                TELEMETRY_ROOM_TYPE_NAME, TelemetryRoomAccountDataEventContent(room_id)
            )
        self._telemetry_room_id = room_id
        return room_id

    async def send_telemetry(
        self, active_users: int, current_ms: float = time.time() * 1000
    ) -> None:
        payload = TelemetryEvent(
            self._instance_id,
            self._hostname,
            int(current_ms),
            TelemetryData(
                TelemetryDataRMAU(
                    allUsers=active_users,
                )
            ),
        )
        self.log.debug(f"Sending telemetry: {payload}")

        try:
            room_id = await self._load_storage_room()
            await self._matrix_client.send_message_event(room_id, TelemetryEventType, payload)
        except:
            self.log.exception("Failed to record telemetry in Matrix")

        if self._endpoint:
            assert self._session
            retries_left = self._retry_count
            while True:
                try:
                    request = self._session.request(
                        str(Method.POST), self._endpoint, data=payload.serialize()
                    )
                    async with request as response:
                        response.raise_for_status()
                        break
                except:
                    self.log.exception("Failed to submit telemetry")
                    if retries_left > 1:
                        self.log.debug(
                            f"Will retry sending telemetry in {self._retry_interval} seconds"
                        )
                        retries_left -= 1
                        await asyncio.sleep(self._retry_interval)
                    else:
                        break
