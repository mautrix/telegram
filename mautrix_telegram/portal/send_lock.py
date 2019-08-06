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
from typing import Dict
from asyncio import Lock

from ..types import TelegramID


class FakeLock:
    async def __aenter__(self) -> None:
        pass

    async def __aexit__(self, exc_type, exc, tb) -> None:
        pass


class PortalSendLock:
    _send_locks: Dict[int, Lock]
    _noop_lock: Lock = FakeLock()

    def __init__(self) -> None:
        self._send_locks = {}

    def __call__(self, user_id: TelegramID, required: bool = True) -> Lock:
        if user_id is None and required:
            raise ValueError("Required send lock for none id")
        try:
            return self._send_locks[user_id]
        except KeyError:
            return (self._send_locks.setdefault(user_id, Lock())
                    if required else self._noop_lock)
