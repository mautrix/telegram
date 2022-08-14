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

from typing import Any, Generator, Tuple, Union
from collections import deque
import hashlib

from telethon.tl.patched import Message, MessageService
from telethon.tl.types import (
    Message,
    MessageMediaContact,
    MessageMediaDice,
    MessageMediaDocument,
    MessageMediaGame,
    MessageMediaGeo,
    MessageMediaPhoto,
    MessageMediaPoll,
    MessageMediaUnsupported,
    MessageService,
    PeerChannel,
    PeerChat,
    PeerUser,
    TypeUpdates,
    UpdateNewChannelMessage,
    UpdateNewMessage,
    UpdateShortChatMessage,
    UpdateShortMessage,
)

from mautrix.types import EventID

from .. import portal as po
from ..types import TelegramID

DedupMXID = Tuple[EventID, TelegramID]
TypeMessage = Union[Message, MessageService, UpdateShortMessage, UpdateShortChatMessage]

media_content_table = {
    MessageMediaContact: lambda media: [media.user_id],
    MessageMediaDocument: lambda media: [media.document.id],
    MessageMediaPhoto: lambda media: [media.photo.id if media.photo else 0],
    MessageMediaGeo: lambda media: [media.geo.long, media.geo.lat],
    MessageMediaGame: lambda media: [media.game.id],
    MessageMediaPoll: lambda media: [media.poll.id],
    MessageMediaDice: lambda media: [media.value, media.emoticon],
    MessageMediaUnsupported: lambda media: ["unsupported media"],
}


class PortalDedup:
    cache_queue_length: int = 256

    _dedup: deque[bytes | int]
    _dedup_mxid: dict[bytes | int, DedupMXID]
    _dedup_action: deque[bytes | int]
    _portal: po.Portal

    def __init__(self, portal: po.Portal) -> None:
        self._dedup = deque()
        self._dedup_mxid = {}
        self._dedup_action = deque(maxlen=self.cache_queue_length)
        self._portal = portal

    @property
    def _always_force_hash(self) -> bool:
        return self._portal.peer_type == "chat"

    def _hash_content(self, event: TypeMessage) -> Generator[Any, None, None]:
        if not self._always_force_hash:
            yield event.id
        yield int(event.date.timestamp())
        if isinstance(event, MessageService):
            yield event.from_id
            yield event.action
        else:
            yield event.message.strip()
            if event.fwd_from:
                yield event.fwd_from.from_id
            if isinstance(event, Message) and event.media:
                media_hash_func = media_content_table.get(type(event.media)) or (
                    lambda media: ["unknown media"]
                )
                yield media_hash_func(event.media)

    def hash_event(self, event: TypeMessage) -> bytes:
        return hashlib.sha256(
            "-".join(str(a) for a in self._hash_content(event)).encode("utf-8")
        ).digest()

    def check_action(self, event: TypeMessage) -> bool:
        dedup_id = self.hash_event(event) if self._always_force_hash else event.id
        if dedup_id in self._dedup_action:
            return True

        self._dedup_action.appendleft(dedup_id)
        return False

    def update(
        self,
        event: TypeMessage,
        mxid: DedupMXID = None,
        expected_mxid: DedupMXID | None = None,
        force_hash: bool = False,
    ) -> tuple[bytes, DedupMXID | None]:
        evt_hash = self.hash_event(event)
        dedup_id = evt_hash if self._always_force_hash or force_hash else event.id
        try:
            found_mxid = self._dedup_mxid[dedup_id]
        except KeyError:
            return evt_hash, None

        if found_mxid != expected_mxid:
            return evt_hash, found_mxid
        self._dedup_mxid[dedup_id] = mxid
        if evt_hash != dedup_id:
            self._dedup_mxid[evt_hash] = mxid
        return evt_hash, None

    def check(
        self, event: TypeMessage, mxid: DedupMXID = None, force_hash: bool = False
    ) -> tuple[bytes, DedupMXID | None]:
        evt_hash = self.hash_event(event)
        dedup_id = evt_hash if self._always_force_hash or force_hash else event.id
        if dedup_id in self._dedup:
            return evt_hash, self._dedup_mxid[dedup_id]

        self._dedup_mxid[dedup_id] = mxid
        self._dedup.appendleft(dedup_id)
        if evt_hash != dedup_id:
            self._dedup_mxid[evt_hash] = mxid
            self._dedup.appendleft(evt_hash)

        while len(self._dedup) > self.cache_queue_length:
            del self._dedup_mxid[self._dedup.pop()]
        return evt_hash, None

    def register_outgoing_actions(self, response: TypeUpdates) -> None:
        for update in response.updates:
            check_dedup = isinstance(
                update, (UpdateNewMessage, UpdateNewChannelMessage)
            ) and isinstance(update.message, MessageService)
            if check_dedup:
                self.check(update.message)
