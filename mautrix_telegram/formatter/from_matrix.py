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
import re
import logging

from telethon.tl.types import *

from .. import user as u, puppet as p
from ..db import Message as DBMessage
from .util import add_surrogates, remove_surrogates

log = logging.getLogger("mau.fmt.mx")


class MatrixParser(HTMLParser):
    mention_regex = re.compile("https://matrix.to/#/(@.+)")

    def __init__(self):
        super().__init__()
        self.text = ""
        self.entities = []
        self._building_entities = {}
        self._list_counter = 0
        self._open_tags = deque()
        self._open_tags_meta = deque()
        self._previous_ended_line = True

    def handle_starttag(self, tag, attrs):
        self._open_tags.appendleft(tag)
        self._open_tags_meta.appendleft(0)
        attrs = dict(attrs)
        entity_type = None
        args = {}
        if tag == "strong" or tag == "b":
            entity_type = MessageEntityBold
        elif tag == "em" or tag == "i":
            entity_type = MessageEntityItalic
        elif tag == "code":
            try:
                pre = self._building_entities["pre"]
                try:
                    pre.language = attrs["class"][len("language-"):]
                except KeyError:
                    pass
            except KeyError:
                entity_type = MessageEntityCode
        elif tag == "pre":
            entity_type = MessageEntityPre
            args["language"] = ""
        elif tag == "a":
            try:
                url = attrs["href"]
            except KeyError:
                return
            mention = self.mention_regex.search(url)
            if mention:
                mxid = mention.group(1)
                user = p.Puppet.get_by_mxid(mxid, create=False)
                if not user:
                    user = u.User.get_by_mxid(mxid, create=False)
                    if not user:
                        return
                if user.username:
                    entity_type = MessageEntityMention
                    url = f"@{user.username}"
                else:
                    entity_type = MessageEntityMentionName
                    args["user_id"] = user.tgid
            elif url.startswith("mailto:"):
                url = url[len("mailto:"):]
                entity_type = MessageEntityEmail
            else:
                if self.get_starttag_text() == url:
                    entity_type = MessageEntityUrl
                else:
                    entity_type = MessageEntityTextUrl
                    args["url"] = url
                    url = None
            self._open_tags_meta.popleft()
            self._open_tags_meta.appendleft(url)

        if entity_type and tag not in self._building_entities:
            offset = len(self.text)
            self._building_entities[tag] = entity_type(offset=offset, length=0, **args)

    def _list_depth(self):
        depth = 0
        for tag in self._open_tags:
            if tag == "ol" or tag == "ul":
                depth += 1
        return depth

    def handle_data(self, text):
        text = unescape(text)
        previous_tag = self._open_tags[0] if len(self._open_tags) > 0 else ""
        list_format_offset = 0
        if previous_tag == "a":
            url = self._open_tags_meta[0]
            if url:
                text = url
        elif len(self._open_tags) > 1 and self._previous_ended_line and previous_tag == "li":
            list_type = self._open_tags[1]
            indent = (self._list_depth() - 1) * 4 * " "
            text = text.strip("\n")
            if len(text) == 0:
                return
            elif list_type == "ul":
                text = f"{indent}* {text}"
                list_format_offset = len(indent) + 2
            elif list_type == "ol":
                n = self._open_tags_meta[1]
                n += 1
                self._open_tags_meta[1] = n
                text = f"{indent}{n}. {text}"
                list_format_offset = len(indent) + 3
        for tag, entity in self._building_entities.items():
            entity.length += len(text.strip("\n"))
            entity.offset += list_format_offset

        if text.endswith("\n"):
            self._previous_ended_line = True
        else:
            self._previous_ended_line = False

        self.text += text

    def handle_endtag(self, tag):
        try:
            self._open_tags.popleft()
            self._open_tags_meta.popleft()
        except IndexError:
            pass
        if (tag == "ul" or tag == "ol") and self.text.endswith("\n"):
            self.text = self.text[:-1]
        entity = self._building_entities.pop(tag, None)
        if entity:
            self.entities.append(entity)


def matrix_to_telegram(html):
    try:
        parser = MatrixParser()
        parser.feed(add_surrogates(html))
        return remove_surrogates(parser.text), parser.entities
    except Exception:
        log.exception("Failed to convert Matrix format:\nhtml=%s", html)


def matrix_reply_to_telegram(content, tg_space, room_id=None):
    try:
        reply = content["m.relates_to"]["m.in_reply_to"]
        room_id = room_id or reply["room_id"]
        event_id = reply["event_id"]
        message = DBMessage.query.filter(DBMessage.mxid == event_id,
                                         DBMessage.tg_space == tg_space,
                                         DBMessage.mx_room == room_id).one_or_none()
        if message:
            return message.tgid
    except KeyError:
        pass
    return None
