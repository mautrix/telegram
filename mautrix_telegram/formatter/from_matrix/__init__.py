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
from typing import Optional, List, Tuple, Callable, Pattern, Match, TYPE_CHECKING
import re
import logging

from telethon.tl.types import (MessageEntityMention, MessageEntityMentionName, MessageEntityItalic,
                               TypeMessageEntity, InputMessageEntityMentionName)
from telethon.helpers import add_surrogate, del_surrogate
from telethon import TelegramClient

from mautrix.types import RoomID, MessageEventContent
from mautrix.util.logging import TraceLogger

from ... import puppet as pu
from ...types import TelegramID
from ...db import Message as DBMessage
from .parser import ParsedMessage, parse_html

if TYPE_CHECKING:
    from ...context import Context

log: TraceLogger = logging.getLogger("mau.fmt.mx")
should_bridge_plaintext_highlights: bool = False

command_regex: Pattern = re.compile(r"^!([A-Za-z0-9@]+)")
not_command_regex: Pattern = re.compile(r"^\\(![A-Za-z0-9@]+)")
plain_mention_regex: Optional[Pattern] = None

MAX_LENGTH = 4096
CUTOFF_TEXT = " [message cut]"
CUT_MAX_LENGTH = MAX_LENGTH - len(CUTOFF_TEXT)


def _cut_long_message(message: str, entities: List[TypeMessageEntity]) -> ParsedMessage:
    if len(message) > MAX_LENGTH:
        message = message[0:CUT_MAX_LENGTH] + CUTOFF_TEXT
        new_entities = []
        for entity in entities:
            if entity.offset > CUT_MAX_LENGTH:
                continue
            if entity.offset + entity.length > CUT_MAX_LENGTH:
                entity.length = CUT_MAX_LENGTH - entity.offset
            new_entities.append(entity)
        new_entities.append(MessageEntityItalic(CUT_MAX_LENGTH, len(CUTOFF_TEXT)))
        entities = new_entities
    return message, entities


class FormatError(Exception):
    pass


def matrix_reply_to_telegram(content: MessageEventContent, tg_space: TelegramID,
                             room_id: Optional[RoomID] = None) -> Optional[TelegramID]:
    event_id = content.get_reply_to()
    if not event_id:
        return
    content.trim_reply_fallback()

    message = DBMessage.get_by_mxid(event_id, room_id, tg_space)
    if message:
        return message.tgid
    return None


async def matrix_to_telegram(client: TelegramClient, *, text: Optional[str] = None,
                             html: Optional[str] = None) -> ParsedMessage:
    if html is not None:
        text, entities = _matrix_html_to_telegram(html)
    elif text is not None:
        text, entities = _matrix_text_to_telegram(text)
    else:
        raise ValueError("text or html must be provided to convert formatting")
    await _fix_name_mentions(client, entities)
    return text, entities


def _matrix_html_to_telegram(html: str) -> ParsedMessage:
    try:
        html = command_regex.sub(r"<command>\1</command>", html)
        html = html.replace("\t", " " * 4)
        html = not_command_regex.sub(r"\1", html)
        if should_bridge_plaintext_highlights:
            html = plain_mention_regex.sub(_plain_mention_to_html, html)

        text, entities = parse_html(add_surrogate(html))
        text = del_surrogate(text.strip())
        text, entities = _cut_long_message(text, entities)

        return text, entities
    except Exception as e:
        raise FormatError(f"Failed to convert Matrix format: {html}") from e


def _matrix_text_to_telegram(text: str) -> ParsedMessage:
    text = command_regex.sub(r"/\1", text)
    text = text.replace("\t", " " * 4)
    text = not_command_regex.sub(r"\1", text)
    if should_bridge_plaintext_highlights:
        entities, pmr_replacer = _plain_mention_to_text()
        text = plain_mention_regex.sub(pmr_replacer, text)
    else:
        entities = []
    return text, entities


async def _fix_name_mentions(client: TelegramClient, entities: List[TypeMessageEntity]) -> None:
    for index in reversed(range(len(entities))):
        entity = entities[index]
        if isinstance(entity, (MessageEntityMentionName, InputMessageEntityMentionName)):
            try:
                user = await client.get_input_entity(entity.user_id)
            except (ValueError, TypeError) as e:
                log.trace(f"Dropping mention of {entity.user_id}: {e}")
                del entities[index]
            else:
                entities[index] = InputMessageEntityMentionName(entity.offset, entity.length, user)


def _plain_mention_to_text() -> Tuple[List[TypeMessageEntity], Callable[[Match], str]]:
    entities = []

    def replacer(match: Match) -> str:
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


def _plain_mention_to_html(match: Match) -> str:
    puppet = pu.Puppet.find_by_displayname(match.group(2))
    if puppet:
        return (f"{match.group(1)}"
                f"<a href='https://matrix.to/#/{puppet.mxid}'>"
                f"{puppet.displayname}"
                "</a>")
    return "".join(match.groups())


def init_mx(context: "Context") -> None:
    global plain_mention_regex, should_bridge_plaintext_highlights
    config = context.config
    dn_template = config["bridge.displayname_template"]
    dn_template = re.escape(dn_template).replace(re.escape("{displayname}"), "[^>]+")
    plain_mention_regex = re.compile(f"^({dn_template})")
    should_bridge_plaintext_highlights = config["bridge.plaintext_highlights"]
