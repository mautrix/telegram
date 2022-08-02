# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2021 Tulir Asokan
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
from __future__ import annotations

import re

from telethon import TelegramClient
from telethon.helpers import add_surrogate, del_surrogate, strip_text
from telethon.tl.types import MessageEntityItalic, TypeMessageEntity

from mautrix.types import MessageEventContent, RoomID

from ...db import Message as DBMessage
from ...types import TelegramID
from .parser import MatrixParser

command_regex = re.compile(r"^!([A-Za-z0-9@]+)")
not_command_regex = re.compile(r"^\\(![A-Za-z0-9@]+)")

MAX_LENGTH = 4096
CUTOFF_TEXT = " [message cut]"
CUT_MAX_LENGTH = MAX_LENGTH - len(CUTOFF_TEXT)


class FormatError(Exception):
    pass


async def matrix_reply_to_telegram(
    content: MessageEventContent, tg_space: TelegramID, room_id: RoomID | None = None
) -> TelegramID | None:
    event_id = content.get_reply_to()
    if not event_id:
        return
    content.trim_reply_fallback()

    message = await DBMessage.get_by_mxid(event_id, room_id, tg_space)
    if message:
        return message.tgid
    return None


async def matrix_to_telegram(
    client: TelegramClient, *, text: str | None = None, html: str | None = None
) -> tuple[str, list[TypeMessageEntity]]:
    if html is not None:
        return await _matrix_html_to_telegram(client, html)
    elif text is not None:
        return _matrix_text_to_telegram(text), []
    else:
        raise ValueError("text or html must be provided to convert formatting")


async def _matrix_html_to_telegram(
    client: TelegramClient, html: str
) -> tuple[str, list[TypeMessageEntity]]:
    try:
        html = command_regex.sub(r"<command>\1</command>", html)
        html = html.replace("\t", " " * 4)
        html = not_command_regex.sub(r"\1", html)

        parsed = await MatrixParser(client).parse(add_surrogate(html))
        text, entities = _cut_long_message(parsed.text, parsed.telegram_entities)
        text = del_surrogate(strip_text(text, entities))

        return text, entities
    except Exception as e:
        raise FormatError(f"Failed to convert Matrix format: {html}") from e


def _cut_long_message(
    message: str, entities: list[TypeMessageEntity]
) -> tuple[str, list[TypeMessageEntity]]:
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


def _matrix_text_to_telegram(text: str) -> str:
    text = command_regex.sub(r"/\1", text)
    text = text.replace("\t", " " * 4)
    text = not_command_regex.sub(r"\1", text)
    return text
