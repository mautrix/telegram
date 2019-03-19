# -*- coding: future_fstrings -*-
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
from typing import Dict, Any
from ..config import DictWithRecursion


def recursive_set(data: Dict[str, Any], key: str, value: Any) -> bool:
    key, next_key = DictWithRecursion._parse_key(key)
    if next_key is not None:
        if key not in data:
            data[key] = {}
        next_data = data.get(key, {})
        if not isinstance(next_data, dict):
            return False
        return recursive_set(next_data, next_key, value)
    data[key] = value
    return True


def recursive_get(data: Dict[str, Any], key: str) -> Any:
    key, next_key = DictWithRecursion._parse_key(key)
    if next_key is not None:
        next_data = data.get(key, None)
        if not next_data:
            return None
        return recursive_get(next_data, next_key)
    return data.get(key, None)


def recursive_del(data: Dict[str, any], key: str) -> bool:
    key, next_key = DictWithRecursion._parse_key(key)
    if next_key is not None:
        if key not in data:
            return False
        next_data = data.get(key, {})
        return recursive_del(next_data, next_key)
    if key in data:
        del data[key]
        return True
    return False
