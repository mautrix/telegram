# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2022 Amir Omidi
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
from typing import Any
from collections import OrderedDict

class LUDict(OrderedDict):
    'Dictionary that evicts the oldest item when it reaches its capacity'
    capacity: int

    def __init__(self, capacity: int):
        self.capacity = capacity
        super().__init__()

    def __setitem__(self, key, value) -> None:
        self._cleanup()
        return super().__setitem__(key, value)

    def __get_item__(self, key) -> Any:
        return super().__getitem__(key)

    def _cleanup(self) -> None:
        while len(self) > self.capacity:
            self.popitem(last=False)
