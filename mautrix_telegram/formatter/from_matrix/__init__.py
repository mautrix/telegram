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
from typing import Optional, List, Tuple, Callable, Pattern, Match, TYPE_CHECKING, Dict, Any
import re
import logging

from telethon.tl.types import (MessageEntityMention, MessageEntityMentionName, MessageEntityItalic,
                               TypeMessageEntity)

from ... import puppet as pu
from ...types import TelegramID, MatrixRoomID
from ...db import Message as DBMessage
from ..util import (add_surrogates, remove_surrogates, trim_reply_fallback_html,
                    trim_reply_fallback_text)
from .parser import ParsedMessage, parse_html

if TYPE_CHECKING:
    from ...context import Context

log = logging.getLogger("mau.fmt.mx")  # type: logging.Logger
should_bridge_plaintext_highlights = False  # type: bool

command_regex = re.compile(r"^!([A-Za-z0-9@]+)")  # type: Pattern
not_command_regex = re.compile(r"^\\(![A-Za-z0-9@]+)")  # type: Pattern
plain_mention_regex = None  # type: Optional[Pattern]


def plain_mention_to_html(match: Match) -> str:
    puppet = pu.Puppet.find_by_displayname(match.group(2))
    if puppet:
        return (f"{match.group(1)}"
                f"<a href='https://matrix.to/#/{puppet.mxid}'>"
                f"{puppet.displayname}"
                "</a>")
    return "".join(match.groups())


def cut_long_message(message: str, entities: List[TypeMessageEntity]) -> ParsedMessage:
    if len(message) > 4096:
        message = message[0:4082] + " [message cut]"
        new_entities = []
        for entity in entities:
            if entity.offset > 4082:
                continue
            if entity.offset + entity.length > 4082:
                entity.length = 4082 - entity.offset
            new_entities.append(entity)
        new_entities.append(MessageEntityItalic(4082, len(" [message cut]")))
        entities = new_entities
    return message, entities


class FormatError(Exception):
    pass


def matrix_to_telegram(html: str) -> ParsedMessage:
    try:
        html = command_regex.sub(r"<command>\1</command>", html)
        html = html.replace("\t", " " * 4)
        html = not_command_regex.sub(r"\1", html)
        if should_bridge_plaintext_highlights:
            html = plain_mention_regex.sub(plain_mention_to_html, html)

        text, entities = parse_html(add_surrogates(html))
        text = remove_surrogates(text.strip())
        text, entities = cut_long_message(text, entities)

        return text, entities
    except Exception as e:
        raise FormatError(f"Failed to convert Matrix format: {html}") from e


def matrix_reply_to_telegram(content: Dict[str, Any], tg_space: TelegramID,
                             room_id: Optional[MatrixRoomID] = None) -> Optional[TelegramID]:
    relates_to = content.get("m.relates_to", None) or {}
    if not relates_to:
        return None
    reply = (relates_to if relates_to.get("rel_type", None) == "m.reference"
             else relates_to.get("m.in_reply_to", None) or {})
    if not reply:
        return None
    room_id = room_id or reply.get("room_id", None)
    event_id = reply.get("event_id", None)
    if not event_id:
        return

    try:
        if content["format"] == "org.matrix.custom.html":
            content["formatted_body"] = trim_reply_fallback_html(content["formatted_body"])
    except KeyError:
        pass
    content["body"] = trim_reply_fallback_text(content["body"])

    message = DBMessage.get_by_mxid(event_id, room_id, tg_space)
    if message:
        return message.tgid
    return None


def matrix_text_to_telegram(text: str) -> ParsedMessage:
    text = command_regex.sub(r"/\1", text)
    text = text.replace("\t", " " * 4)
    text = not_command_regex.sub(r"\1", text)
    if should_bridge_plaintext_highlights:
        entities, pmr_replacer = plain_mention_to_text()
        text = plain_mention_regex.sub(pmr_replacer, text)
    else:
        entities = []
    return text, entities


def plain_mention_to_text() -> Tuple[List[TypeMessageEntity], Callable[[str], str]]:
    entities = []

    def replacer(match) -> str:
        puppet = pu.Puppet.find_by_displayname(match.group(2))
        if puppet:
            offset = match.start()
            length = match.end() - offset
            if puppet.username:
                entity = MessageEntityMention(offset, length)
                text = f"@{puppet.username}"
            else:
                entity = MessageEntityMentionName(offset, length, user_id=puppet.tgid)
                text = puppet.displayname
            entities.append(entity)
            return text
        return "".join(match.groups())

    return entities, replacer


def init_mx(context: "Context") -> None:
    global plain_mention_regex, should_bridge_plaintext_highlights
    config = context.config
    dn_template = config.get("bridge.displayname_template", "{displayname} (Telegram)")
    dn_template = re.escape(dn_template).replace(re.escape("{displayname}"), "[^>]+")
    plain_mention_regex = re.compile(f"^({dn_template})")
    should_bridge_plaintext_highlights = config["bridge.plaintext_highlights"] or False
