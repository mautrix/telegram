# -*- coding: future_fstrings -*-
# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2018 Tulir Asokan
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
from html import unescape
from html.parser import HTMLParser
from collections import deque
import math
import re
import logging

from telethon_aio.tl.types import *

from .. import user as u, puppet as pu, portal as po
from ..db import Message as DBMessage
from .util import (add_surrogates, remove_surrogates, trim_reply_fallback_html,
                   trim_reply_fallback_text, html_to_unicode)

log = logging.getLogger("mau.fmt.mx")


class MatrixParser(HTMLParser):
    mention_regex = re.compile("https://matrix.to/#/(@.+:.+)")
    room_regex = re.compile("https://matrix.to/#/(#.+:.+)")
    block_tags = ("br", "p", "pre", "blockquote",
                  "ol", "ul", "li",
                  "h1", "h2", "h3", "h4", "h5", "h6",
                  "div", "hr", "table")

    def __init__(self):
        super().__init__()
        self.text = ""
        self.entities = []
        self._building_entities = {}
        self._list_counter = 0
        self._open_tags = deque()
        self._open_tags_meta = deque()
        self._line_is_new = True
        self._list_entry_is_new = False

    def _parse_url(self, url, args):
        mention = self.mention_regex.match(url)
        if mention:
            mxid = mention.group(1)
            user = (pu.Puppet.get_by_mxid(mxid)
                    or u.User.get_by_mxid(mxid, create=False))
            if not user:
                return None, None
            if user.username:
                return MessageEntityMention, f"@{user.username}"
            else:
                args["user_id"] = InputUser(user.tgid, 0)
                return InputMessageEntityMentionName, user.displayname or None

        room = self.room_regex.match(url)
        if room:
            username = po.Portal.get_username_from_mx_alias(room.group(1))
            portal = po.Portal.find_by_username(username)
            if portal and portal.username:
                return MessageEntityMention, f"@{portal.username}"

        if url.startswith("mailto:"):
            return MessageEntityEmail, url[len("mailto:"):]
        elif self.get_starttag_text() == url:
            return MessageEntityUrl, url
        else:
            args["url"] = url
            return MessageEntityTextUrl, None

    def handle_starttag(self, tag, attrs):
        self._open_tags.appendleft(tag)
        self._open_tags_meta.appendleft(0)

        attrs = dict(attrs)
        entity_type = None
        args = {}
        if tag in ("strong", "b"):
            entity_type = MessageEntityBold
        elif tag in ("em", "i"):
            entity_type = MessageEntityItalic
        elif tag == "code":
            try:
                pre = self._building_entities["pre"]
                try:
                    # Pre tag and language found, add language to MessageEntityPre
                    pre.language = attrs["class"][len("language-"):]
                except KeyError:
                    # Pre tag found, but language not found, keep pre as-is
                    pass
            except KeyError:
                # No pre tag found, this is inline code
                entity_type = MessageEntityCode
        elif tag == "pre":
            entity_type = MessageEntityPre
            args["language"] = ""
        elif tag == "command":
            entity_type = MessageEntityBotCommand
        elif tag == "li":
            self._list_entry_is_new = True
        elif tag == "a":
            try:
                url = attrs["href"]
            except KeyError:
                return
            entity_type, url = self._parse_url(url, args)
            self._open_tags_meta.popleft()
            self._open_tags_meta.appendleft(url)

        if tag in self.block_tags:
            self._newline()

        if entity_type and tag not in self._building_entities:
            offset = len(self.text)
            self._building_entities[tag] = entity_type(offset=offset, length=0, **args)

    @property
    def _list_indent(self):
        indent = 0
        first_skipped = False
        for index, tag in enumerate(self._open_tags):
            if not first_skipped and tag in ("ol", "ul"):
                # The first list level isn't indented, so skip it.
                first_skipped = True
                continue
            if tag == "ol":
                n = self._open_tags_meta[index]
                extra_length_for_long_index = (int(math.log(n, 10)) - 1) * 3
                indent += 4 + extra_length_for_long_index
            elif tag == "ul":
                indent += 3
        return indent

    def _newline(self, allow_multi=False):
        if self._line_is_new and not allow_multi:
            return
        self.text += "\n"
        self._line_is_new = True
        for entity in self._building_entities.values():
            entity.length += 1

    def handle_data(self, text):
        text = unescape(text)
        previous_tag = self._open_tags[0] if len(self._open_tags) > 0 else ""
        extra_offset = 0
        if previous_tag == "a":
            url = self._open_tags_meta[0]
            if url:
                text = url
        elif previous_tag == "command":
            text = f"/{text}"

        strikethrough, underline = "del" in self._open_tags, "u" in self._open_tags
        if strikethrough and underline:
            text = html_to_unicode(text, "\u0336\u0332")
        elif strikethrough:
            text = html_to_unicode(text, "\u0336")
        elif underline:
            text = html_to_unicode(text, "\u0332")

        list_entry_handled_once = False
        # In order to maintain order of things like blockquotes in lists or lists in blockquotes,
        # we can't just have ifs/elses and we need to actually loop through the open tags in order.
        for index, tag in enumerate(self._open_tags):
            if tag == "blockquote" and self._line_is_new:
                text = f"> {text}"
                extra_offset += 2
            elif tag == "li" and not list_entry_handled_once:
                list_type_index = index + 1
                list_type = self._open_tags[list_type_index]
                indent = self._list_indent * " " if self._line_is_new else ""
                if list_type == "ol":
                    n = self._open_tags_meta[list_type_index]
                    if self._list_entry_is_new:
                        n += 1
                        self._open_tags_meta[list_type_index] = n
                        prefix = f"{n}. "
                    else:
                        prefix = int(math.log(n, 10)) * 3 * " " + 4 * " "
                else:
                    prefix = "* " if self._list_entry_is_new else 3 * " "
                if not self._list_entry_is_new and not self._line_is_new:
                    prefix = ""
                extra_offset += len(indent) + len(prefix)
                text = indent + prefix + text
                self._list_entry_is_new = False
                list_entry_handled_once = True
        for tag, entity in self._building_entities.items():
            entity.length += len(text) - extra_offset
            entity.offset += extra_offset

        self._line_is_new = False
        self.text += text

    def handle_endtag(self, tag):
        try:
            self._open_tags.popleft()
            self._open_tags_meta.popleft()
        except IndexError:
            pass

        entity = self._building_entities.pop(tag, None)
        if entity:
            self.entities.append(entity)

        if tag in self.block_tags:
            self._newline(allow_multi=tag == "br")


command_regex = re.compile("(\s|^)!([A-Za-z0-9@]+)")


def matrix_text_to_telegram(text):
    text = command_regex.sub(r"\1/\2", text)
    return text


def matrix_to_telegram(html):
    try:
        parser = MatrixParser()
        html = html.replace("\n", "")
        html = command_regex.sub(r"\1<command>\2</command>", html)
        parser.feed(add_surrogates(html))
        return remove_surrogates(parser.text.strip()), parser.entities
    except Exception:
        log.exception("Failed to convert Matrix format:\nhtml=%s", html)


def matrix_reply_to_telegram(content, tg_space, room_id=None):
    try:
        reply = content["m.relates_to"]["m.in_reply_to"]
        room_id = room_id or reply["room_id"]
        event_id = reply["event_id"]

        try:
            if content["format"] == "org.matrix.custom.html":
                content["formatted_body"] = trim_reply_fallback_html(content["formatted_body"])
        except KeyError:
            pass
        content["body"] = trim_reply_fallback_text(content["body"])

        message = DBMessage.query.filter(DBMessage.mxid == event_id,
                                         DBMessage.tg_space == tg_space,
                                         DBMessage.mx_room == room_id).one_or_none()
        if message:
            return message.tgid
    except KeyError:
        pass
    return None
