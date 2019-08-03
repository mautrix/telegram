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
from typing import Optional, Pattern
import struct
import re


# add_surrogates and remove_surrogates are unicode surrogate utility functions from Telethon.
# Licensed under the MIT license.
# https://github.com/LonamiWebs/Telethon/blob/7cce7aa3e4c6c7019a55530391b1761d33e5a04e/telethon/helpers.py
def add_surrogates(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    return "".join("".join(chr(y) for y in struct.unpack("<HH", x.encode("utf-16-le")))
                   if (0x10000 <= ord(x) <= 0x10FFFF) else x for x in text)


def remove_surrogates(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    return text.encode("utf-16", "surrogatepass").decode("utf-16")
