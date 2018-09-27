# -*- coding: future_fstrings -*-
# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2018 Tulir Asokan
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
import re
from typing import List, Tuple, Pattern
from telethon.tl.types import TypeMessageEntity


class MatrixParserCommon:
    mention_regex = re.compile("https://matrix.to/#/(@.+:.+)")  # type: Pattern
    room_regex = re.compile("https://matrix.to/#/(#.+:.+)")  # type: Pattern
    block_tags = ("p", "pre", "blockquote",
                  "ol", "ul", "li",
                  "h1", "h2", "h3", "h4", "h5", "h6",
                  "div", "hr", "table")  # type: Tuple[str, ...]
    list_bullets = ("●", "○", "■", "‣")  # type: Tuple[str, ...]

    @classmethod
    def list_bullet(cls, depth: int) -> str:
        return cls.list_bullets[(depth - 1) % len(cls.list_bullets)] + " "


ParsedMessage = Tuple[str, List[TypeMessageEntity]]
