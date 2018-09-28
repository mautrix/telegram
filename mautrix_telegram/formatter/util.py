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
from typing import Optional, Pattern
from html import escape
import struct
import re


def unicode_to_html(text: str, html: str, ctrl: str, tag: str) -> str:
    if ctrl not in text:
        return html
    if not html:
        html = escape(text)
    tag_start = f"<{tag}>"
    tag_end = f"</{tag}>"
    characters = html.split(ctrl)
    html = ""
    in_tag = False
    for char in characters:
        if not in_tag:
            if len(char) > 1:
                html += char[0:-1]
                char = char[-1]
            html += tag_start
            in_tag = True
            html += char
        else:
            if len(char) > 1:
                html += tag_end
                in_tag = False
            html += char
    if in_tag:
        html += tag_end
    return html


def html_to_unicode(text: str, ctrl: str) -> str:
    return ctrl.join(text) + ctrl


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


# trim_reply_fallback_text, html_reply_fallback_regex and trim_reply_fallback_html are Matrix
# reply fallback utility functions.
# You may copy and use them under any OSI-approved license.
def trim_reply_fallback_text(text: str) -> str:
    if not text.startswith("> ") or "\n" not in text:
        return text
    lines = text.split("\n")
    while len(lines) > 0 and lines[0].startswith("> "):
        lines.pop(0)
    return "\n".join(lines)


html_reply_fallback_regex = re.compile("^<mx-reply>"
                                       r"[\s\S]+?"
                                       "</mx-reply>")  # type: Pattern


def trim_reply_fallback_html(html: str) -> str:
    return html_reply_fallback_regex.sub("", html)
