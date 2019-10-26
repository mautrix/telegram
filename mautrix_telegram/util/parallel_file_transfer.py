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
from typing import Optional, List, AsyncGenerator, Union, Awaitable, DefaultDict
from collections import defaultdict
import asyncio
import logging
import time
import math

from telethon.tl.types import (Document, InputFileLocation, InputDocumentFileLocation,
                               InputPhotoFileLocation, InputPeerPhotoFileLocation)
from telethon.tl.functions.auth import ExportAuthorizationRequest, ImportAuthorizationRequest
from telethon.tl.functions.upload import GetFileRequest
from telethon.network import MTProtoSender
from telethon.crypto import AuthKey
from telethon import utils

from mautrix.appservice import IntentAPI

from ..tgclient import MautrixTelegramClient
from ..db import TelegramFile as DBTelegramFile

log: logging.Logger = logging.getLogger("mau.util")

TypeLocation = Union[Document, InputDocumentFileLocation, InputPeerPhotoFileLocation,
                     InputFileLocation, InputPhotoFileLocation]


class Sender:
    sender: MTProtoSender
    request: GetFileRequest
    remaining: int
    stride: int

    def __init__(self, sender: MTProtoSender, file: TypeLocation, offset: int, limit: int,
                 stride: int, count: int) -> None:
        log.debug(f"Creating sender with {offset=} {limit=} {stride=} {count=}")
        self.sender = sender
        self.request = GetFileRequest(file, offset=offset, limit=limit)
        self.stride = stride
        self.remaining = count

    async def next(self) -> Optional[bytes]:
        if not self.remaining:
            return None
        log.debug(f"Sending {self.request!s}")
        result = await self.sender.send(self.request)
        self.remaining -= 1
        self.request.offset += self.stride
        return result.bytes

    def disconnect(self) -> Awaitable[None]:
        return self.sender.disconnect()


class ParallelDownloader:
    client: MautrixTelegramClient
    loop: asyncio.AbstractEventLoop
    dc_id: int
    senders: Optional[List[Sender]]
    auth_key: AuthKey

    def __init__(self, client: MautrixTelegramClient, dc_id: int) -> None:
        self.client = client
        self.loop = self.client.loop
        self.dc_id = dc_id
        self.exported = dc_id and self.client.session.dc_id != dc_id
        self.auth_key = self.client.session.auth_key if not self.exported else None
        self.senders = None

    async def _init(self, connections: int, file: TypeLocation, part_count: int, part_size: int
                    ) -> None:
        minimum, remainder = divmod(part_count, connections)

        def get_part_count() -> int:
            nonlocal remainder
            if remainder > 0:
                remainder -= 1
                return minimum + 1
            return minimum

        self.senders = [
            await self._create_sender(file, 0, part_size, connections * part_size,
                                      get_part_count()),
            *await asyncio.gather(*[
                self._create_sender(file, i, part_size, connections * part_size, get_part_count())
                for i in range(1, connections)
            ])
        ]

    async def _cleanup(self) -> None:
        await asyncio.gather(*[sender.disconnect() for sender in self.senders])
        self.senders = None

    async def _create_sender(self, file: TypeLocation, index: int, part_size: int, stride: int,
                             part_count: int) -> Sender:
        dc = await self.client._get_dc(self.dc_id)
        sender = MTProtoSender(self.auth_key, self.loop, loggers=self.client._log)
        await sender.connect(self.client._connection(dc.ip_address, dc.port, dc.id,
                                                     loop=self.loop, loggers=self.client._log,
                                                     proxy=self.client._proxy))
        if not self.auth_key:
            log.debug(f"Exporting auth to DC {self.dc_id}")
            auth = await self.client(ExportAuthorizationRequest(self.dc_id))
            req = self.client._init_with(ImportAuthorizationRequest(
                id=auth.id, bytes=auth.bytes
            ))
            await sender.send(req)
            self.auth_key = sender.auth_key
        return Sender(sender, file, index * part_size, part_size, stride, part_count)

    @staticmethod
    def _get_connection_count(file_size: int, max_count: int = 20,
                              full_size: int = 100 * 1024 * 1024) -> int:
        if file_size > full_size:
            return max_count
        return math.ceil((file_size / full_size) * max_count)

    async def download(self, file: TypeLocation, file_size: int,
                       part_size_kb: Optional[float] = None,
                       connection_count: Optional[int] = None) -> AsyncGenerator[bytes, None]:
        connection_count = connection_count or self._get_connection_count(file_size)
        part_size = (part_size_kb or utils.get_appropriated_part_size(file_size)) * 1024
        part_count = math.ceil(file_size / part_size)
        log.debug("Starting parallel download: "
                  f"{connection_count} {part_size} {part_count} {file!s}")
        await self._init(connection_count, file, part_count, part_size)

        part = 0
        while part < part_count:
            tasks = []
            for sender in self.senders:
                tasks.append(self.loop.create_task(sender.next()))
            for task in tasks:
                data = await task
                if not data:
                    break
                yield data
                part += 1
                log.debug(f"Part {part} downloaded")

        log.debug("Parallel download finished, cleaning up connections")
        await self._cleanup()


parallel_transfer_locks: DefaultDict[int, asyncio.Lock] = defaultdict(lambda: asyncio.Lock())


async def parallel_transfer_to_matrix(client: MautrixTelegramClient, intent: IntentAPI,
                                      loc_id: str, location: TypeLocation, filename: str,
                                      parallel_id: int) -> DBTelegramFile:
    size = location.size
    mime_type = location.mime_type
    dc_id, location = utils.get_input_location(location)
    # We lock the transfers because telegram has connection count limits
    async with parallel_transfer_locks[parallel_id]:
        downloader = ParallelDownloader(client, dc_id)
        content_uri = await intent.upload_media(downloader.download(location, size),
                                                mime_type=mime_type, filename=filename, size=size)
    return DBTelegramFile(id=loc_id, mxc=content_uri, mime_type=mime_type,
                          was_converted=False, timestamp=int(time.time()), size=size,
                          width=None, height=None)
