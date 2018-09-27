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
from typing import (Optional, List, Tuple, Type, Dict, Any, TYPE_CHECKING, Match)
from html import unescape
from html.parser import HTMLParser
from collections import deque
import math

from telethon.tl.types import (MessageEntityMention, MessageEntityMentionName, MessageEntityEmail,
                               MessageEntityUrl, MessageEntityTextUrl, MessageEntityBold,
                               MessageEntityItalic, MessageEntityCode, MessageEntityPre,
                               MessageEntityBotCommand, TypeMessageEntity)

from ... import user as u, puppet as pu, portal as po
from ...types import MatrixUserID
from ..util import html_to_unicode
from .parser_common import MatrixParserCommon, ParsedMessage

if TYPE_CHECKING:
    from typing import Deque


def parse_html(html: str) -> ParsedMessage:
    parser = MatrixParser()
    parser.feed(html)
    return parser.text, parser.entities


class MatrixParser(HTMLParser, MatrixParserCommon):
    def __init__(self):
        super(MatrixParser, self).__init__()
        self.text = ""  # type: str
        self.entities = []  # type: List[TypeMessageEntity]
        self._building_entities = {}  # type: Dict[str, TypeMessageEntity]
        self._list_counter = 0  # type: int
        self._open_tags = deque()  # type: Deque[str]
        self._open_tags_meta = deque()  # type: Deque[Any]
        self._line_is_new = True  # type: bool
        self._list_entry_is_new = False  # type: bool

    def _parse_url(self, url: str, args: Dict[str, Any]
                   ) -> Tuple[Optional[Type[TypeMessageEntity]], Optional[str]]:
        mention = self.mention_regex.match(url)  # type: Match
        if mention:
            mxid = MatrixUserID(mention.group(1))
            user = (pu.Puppet.get_by_mxid(mxid)
                    or u.User.get_by_mxid(mxid, create=False))
            if not user:
                return None, None
            if user.username:
                return MessageEntityMention, f"@{user.username}"
            elif user.tgid:
                args["user_id"] = user.tgid
                return MessageEntityMentionName, user.displayname or None
            else:
                return None, None

        room = self.room_regex.match(url)  # type: Match
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

    def handle_starttag(self, tag: str, attrs_list: List[Tuple[str, str]]):
        self._open_tags.appendleft(tag)
        self._open_tags_meta.appendleft(0)

        attrs = dict(attrs_list)
        entity_type = None  # type: Optional[Type[TypeMessageEntity]]
        args = {}  # type: Dict[str, Any]
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

        if (tag in self.block_tags and ("blockquote" not in self._open_tags)) or tag == "br":
            self._newline()

        if entity_type and tag not in self._building_entities:
            offset = len(self.text)
            self._building_entities[tag] = entity_type(offset=offset, length=0, **args)

    @property
    def _list_indent(self) -> int:
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

    def _newline(self, allow_multi: bool = False):
        if self._line_is_new and not allow_multi:
            return
        self.text += "\n"
        self._line_is_new = True
        for entity in self._building_entities.values():
            entity.length += 1

    def _handle_special_previous_tags(self, text: str) -> str:
        if "pre" not in self._open_tags and "code" not in self._open_tags:
            text = text.replace("\n", "")
        else:
            text = text.strip()

        previous_tag = self._open_tags[0] if len(self._open_tags) > 0 else ""
        if previous_tag == "a":
            url = self._open_tags_meta[0]
            if url:
                text = url
        elif previous_tag == "command":
            text = f"/{text}"
        return text

    def _html_to_unicode(self, text: str) -> str:
        strikethrough, underline = "del" in self._open_tags, "u" in self._open_tags
        if strikethrough and underline:
            text = html_to_unicode(text, "\u0336\u0332")
        elif strikethrough:
            text = html_to_unicode(text, "\u0336")
        elif underline:
            text = html_to_unicode(text, "\u0332")
        return text

    def _handle_tags_for_data(self, text: str) -> Tuple[str, int]:
        extra_offset = 0
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
                    prefix = (self.list_bullet(self._open_tags.count('ul'))
                              if self._list_entry_is_new else 3 * " ")
                if not self._list_entry_is_new and not self._line_is_new:
                    prefix = ""
                extra_offset += len(indent) + len(prefix)
                text = indent + prefix + text
                self._list_entry_is_new = False
                list_entry_handled_once = True
        return text, extra_offset

    def _extend_entities_in_construction(self, text: str, extra_offset: int):
        for tag, entity in self._building_entities.items():
            entity.length += len(text) - extra_offset
            entity.offset += extra_offset

    def handle_data(self, text: str):
        text = unescape(text)
        text = self._handle_special_previous_tags(text)
        text = self._html_to_unicode(text)
        text, extra_offset = self._handle_tags_for_data(text)
        self._extend_entities_in_construction(text, extra_offset)
        self._line_is_new = False
        self.text += text

    def handle_endtag(self, tag: str):
        try:
            self._open_tags.popleft()
            self._open_tags_meta.popleft()
        except IndexError:
            pass

        entity = self._building_entities.pop(tag, None)
        if entity:
            self.entities.append(entity)

        if tag in self.block_tags and tag != "br" and "blockquote" not in self._open_tags:
            self._newline(allow_multi=tag == "br")
