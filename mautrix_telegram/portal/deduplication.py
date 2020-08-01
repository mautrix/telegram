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
from typing import Optional, Deque, Dict, Tuple, TYPE_CHECKING
from collections import deque
import hashlib

from telethon.tl.patched import Message, MessageService
from telethon.tl.types import (MessageMediaContact, MessageMediaDocument, MessageMediaGeo,
                               MessageMediaPhoto, TypeMessage, TypeUpdates, UpdateNewMessage,
                               UpdateNewChannelMessage)

from mautrix.types import EventID

from ..context import Context
from ..types import TelegramID

if TYPE_CHECKING:
    from .base import BasePortal

DedupMXID = Tuple[EventID, TelegramID]


class PortalDedup:
    pre_db_check: bool = False
    cache_queue_length: int = 20

    _dedup: Deque[str]
    _dedup_mxid: Dict[str, DedupMXID]
    _dedup_action: Deque[str]
    _portal: 'BasePortal'

    def __init__(self, portal: 'BasePortal') -> None:
        self._dedup = deque()
        self._dedup_mxid = {}
        self._dedup_action = deque()
        self._portal = portal

    @property
    def _always_force_hash(self) -> bool:
        return self._portal.peer_type == 'chat'

    @staticmethod
    def _hash_event(event: TypeMessage) -> str:
        # Non-channel messages are unique per-user (wtf telegram), so we have no other choice than
        # to deduplicate based on a hash of the message content.

        # The timestamp is only accurate to the second, so we can't rely solely on that either.
        if isinstance(event, MessageService):
            hash_content = [event.date.timestamp(), event.from_id, event.action]
        else:
            hash_content = [event.date.timestamp(), event.message]
            if event.fwd_from:
                hash_content += [event.fwd_from.from_id, event.fwd_from.channel_id]
            elif isinstance(event, Message) and event.media:
                try:
                    hash_content += {
                        MessageMediaContact: lambda media: [media.user_id],
                        MessageMediaDocument: lambda media: [media.document.id],
                        MessageMediaPhoto: lambda media: [media.photo.id if media.photo else 0],
                        MessageMediaGeo: lambda media: [media.geo.long, media.geo.lat],
                    }[type(event.media)](event.media)
                except KeyError:
                    pass
        return hashlib.md5("-"
                           .join(str(a) for a in hash_content)
                           .encode("utf-8")
                           ).hexdigest()

    def check_action(self, event: TypeMessage) -> bool:
        evt_hash = self._hash_event(event) if self._always_force_hash else event.id
        if evt_hash in self._dedup_action:
            return True

        self._dedup_action.append(evt_hash)

        if len(self._dedup_action) > self.cache_queue_length:
            self._dedup_action.popleft()
        return False

    def update(self, event: TypeMessage, mxid: DedupMXID = None,
               expected_mxid: Optional[DedupMXID] = None, force_hash: bool = False
               ) -> Optional[DedupMXID]:
        evt_hash = self._hash_event(event) if self._always_force_hash or force_hash else event.id
        try:
            found_mxid = self._dedup_mxid[evt_hash]
        except KeyError:
            return EventID("None"), TelegramID(0)

        if found_mxid != expected_mxid:
            return found_mxid
        self._dedup_mxid[evt_hash] = mxid
        return None

    def check(self, event: TypeMessage, mxid: DedupMXID = None, force_hash: bool = False
              ) -> Optional[DedupMXID]:
        evt_hash = (self._hash_event(event)
                    if self._always_force_hash or force_hash
                    else event.id)
        if evt_hash in self._dedup:
            return self._dedup_mxid[evt_hash]

        self._dedup_mxid[evt_hash] = mxid
        self._dedup.append(evt_hash)

        if len(self._dedup) > self.cache_queue_length:
            del self._dedup_mxid[self._dedup.popleft()]
        return None

    def register_outgoing_actions(self, response: TypeUpdates) -> None:
        for update in response.updates:
            check_dedup = (isinstance(update, (UpdateNewMessage, UpdateNewChannelMessage))
                           and isinstance(update.message, MessageService))
            if check_dedup:
                self.check(update.message)


def init(context: Context) -> None:
    cfg = context.config
    PortalDedup.dedup_pre_db_check = cfg["bridge.deduplication.pre_db_check"]
    PortalDedup.dedup_cache_queue_length = cfg["bridge.deduplication.cache_queue_length"]
