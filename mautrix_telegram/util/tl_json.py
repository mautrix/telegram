# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2022 Tulir Asokan
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
from telethon.tl.types import (
    JsonArray,
    JsonBool,
    JsonNull,
    JsonNumber,
    JsonObject,
    JsonObjectValue,
    JsonString,
    TypeJSONValue,
)

from mautrix.types import JSON


def parse_tl_json(val: TypeJSONValue) -> JSON:
    if isinstance(val, JsonObject):
        return {entry.key: parse_tl_json(entry.value) for entry in val.value}
    elif isinstance(val, JsonArray):
        return [parse_tl_json(item) for item in val.value]
    elif isinstance(val, (JsonBool, JsonNumber, JsonString)):
        return val.value
    elif isinstance(val, JsonNull):
        return None
    raise ValueError(f"Unsupported type {type(val)} in TL JSON object")
