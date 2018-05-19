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
from html import unescape
from html.parser import HTMLParser
from collections import deque
from typing import Optional, List, Tuple, Type, Callable, Dict, Any
import math
import re
import logging

from telethon.tl.types import (MessageEntityMention,
                               InputMessageEntityMentionName, MessageEntityEmail,
                               MessageEntityUrl, MessageEntityTextUrl, MessageEntityBold,
                               MessageEntityItalic, MessageEntityCode, MessageEntityPre,
                               MessageEntityBotCommand, InputUser, TypeMessageEntity)

from ..context import Context
from .. import user as u, puppet as pu, portal as po
from ..db import Message as DBMessage
from .util import (add_surrogates, remove_surrogates, trim_reply_fallback_html,
                   trim_reply_fallback_text, html_to_unicode)

log = logging.getLogger("mau.fmt.mx")
should_bridge_plaintext_highlights = False


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

    def _parse_url(self, url: str, args: Dict[str, Any]
                   ) -> Tuple[Optional[Type[TypeMessageEntity]], Optional[str]]:
        mention = self.mention_regex.match(url)
        if mention:
            mxid = mention.group(1)
            user = (pu.Puppet.get_by_mxid(mxid)
                    or u.User.get_by_mxid(mxid, create=False))
            if not user:
                return None, None
            if user.username:
                return MessageEntityMention, f"@{user.username}"
            elif user.tgid:
                args["user_id"] = InputUser(user.tgid, 0)
                return InputMessageEntityMentionName, user.displayname or None
            else:
                return None, None

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

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, str]]):
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

        if tag in self.block_tags and ("blockquote" not in self._open_tags or tag == "br"):
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
                    prefix = "* " if self._list_entry_is_new else 3 * " "
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


command_regex = re.compile(r"^!([A-Za-z0-9@]+)")
not_command_regex = re.compile(r"^\\(![A-Za-z0-9@]+)")
plain_mention_regex = None


def plain_mention_to_html(match):
    puppet = pu.Puppet.find_by_displayname(match.group(2))
    if puppet:
        return (f"{match.group(1)}"
                f"<a href='https://matrix.to/#/{puppet.mxid}'>"
                f"{puppet.displayname}"
                "</a>")
    return "".join(match.groups())


def matrix_to_telegram(html: str) -> Tuple[str, List[TypeMessageEntity]]:
    try:
        parser = MatrixParser()
        html = command_regex.sub(r"<command>\1</command>", html)
        html = not_command_regex.sub(r"\1", html)
        if should_bridge_plaintext_highlights:
            html = plain_mention_regex.sub(plain_mention_to_html, html)
        parser.feed(add_surrogates(html))
        return remove_surrogates(parser.text.strip()), parser.entities
    except Exception:
        log.exception("Failed to convert Matrix format:\nhtml=%s", html)


def matrix_reply_to_telegram(content: dict, tg_space: int, room_id: Optional[str] = None
                             ) -> Optional[int]:
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


def matrix_text_to_telegram(text: str) -> Tuple[str, List[TypeMessageEntity]]:
    text = command_regex.sub(r"/\1", text)
    text = not_command_regex.sub(r"\1", text)
    if should_bridge_plaintext_highlights:
        entities, pmr_replacer = plain_mention_to_text()
        text = plain_mention_regex.sub(pmr_replacer, text)
    else:
        entities = []
    return text, entities


def plain_mention_to_text() -> Tuple[List[TypeMessageEntity], Callable[[str], str]]:
    entities = []

    def replacer(match):
        puppet = pu.Puppet.find_by_displayname(match.group(2))
        if puppet:
            offset = match.start()
            length = match.end() - offset
            if puppet.username:
                entity = MessageEntityMention(offset, length)
                text = f"@{puppet.username}"
            else:
                entity = InputMessageEntityMentionName(offset, length,
                                                       user_id=InputUser(puppet.tgid, 0))
                text = puppet.displayname
            entities.append(entity)
            return text
        return "".join(match.groups())

    return entities, replacer


def init_mx(context: Context):
    global plain_mention_regex, should_bridge_plaintext_highlights
    config = context.config
    dn_template = config.get("bridge.displayname_template", "{displayname} (Telegram)")
    dn_template = re.escape(dn_template).replace(re.escape("{displayname}"), "[^>]+")
    plain_mention_regex = re.compile(f"(\s|^)({dn_template})")
    should_bridge_plaintext_highlights = config["bridge.plaintext_highlights"] or False
