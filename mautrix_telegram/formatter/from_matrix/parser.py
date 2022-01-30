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

import logging

from telethon import TelegramClient

from mautrix.types import RoomID, UserID
from mautrix.util.formatter import HTMLNode, MatrixParser as BaseMatrixParser, RecursionContext
from mautrix.util.logging import TraceLogger

from ... import portal as po, puppet as pu, user as u
from .telegram_message import TelegramEntityType, TelegramMessage

log: TraceLogger = logging.getLogger("mau.fmt.mx")


class MatrixParser(BaseMatrixParser[TelegramMessage]):
    e = TelegramEntityType
    fs = TelegramMessage
    client: TelegramClient

    def __init__(self, client: TelegramClient) -> None:
        self.client = client

    async def custom_node_to_fstring(
        self, node: HTMLNode, ctx: RecursionContext
    ) -> TelegramMessage | None:
        if node.tag == "command":
            msg = await self.tag_aware_parse_node(node, ctx)
            return msg.prepend("/").format(TelegramEntityType.COMMAND)
        return None

    async def user_pill_to_fstring(self, msg: TelegramMessage, user_id: UserID) -> TelegramMessage:
        user = await pu.Puppet.get_by_mxid(user_id) or await u.User.get_by_mxid(
            user_id, create=False
        )
        if not user:
            return msg
        if user.tg_username:
            return TelegramMessage(f"@{user.tg_username}").format(TelegramEntityType.MENTION)
        elif user.tgid:
            displayname = user.plain_displayname or msg.text
            msg = TelegramMessage(displayname)
            try:
                input_entity = await self.client.get_input_entity(user.tgid)
            except (ValueError, TypeError) as e:
                log.trace(f"Dropping mention of {user.tgid}: {e}")
            else:
                msg = msg.format(TelegramEntityType.MENTION_NAME, user_id=input_entity)
        return msg

    async def url_to_fstring(self, msg: TelegramMessage, url: str) -> TelegramMessage:
        if url == msg.text:
            return msg.format(self.e.URL)
        else:
            return msg.format(self.e.INLINE_URL, url=url)

    async def room_pill_to_fstring(self, msg: TelegramMessage, room_id: RoomID) -> TelegramMessage:
        username = po.Portal.get_username_from_mx_alias(room_id)
        portal = await po.Portal.find_by_username(username)
        if portal and portal.username:
            return TelegramMessage(f"@{portal.username}").format(TelegramEntityType.MENTION)

    async def header_to_fstring(self, node: HTMLNode, ctx: RecursionContext) -> TelegramMessage:
        children = await self.node_to_fstrings(node, ctx)
        length = int(node.tag[1])
        prefix = "#" * length + " "
        return TelegramMessage.join(children, "").prepend(prefix).format(TelegramEntityType.BOLD)

    async def blockquote_to_fstring(
        self, node: HTMLNode, ctx: RecursionContext
    ) -> TelegramMessage:
        msg = await self.tag_aware_parse_node(node, ctx)
        children = msg.trim().split("\n")
        children = [child.prepend("> ") for child in children]
        return TelegramMessage.join(children, "\n")

    async def color_to_fstring(self, msg: TelegramMessage, color: str) -> TelegramMessage:
        return msg

    async def spoiler_to_fstring(self, msg: TelegramMessage, reason: str) -> TelegramMessage:
        msg = msg.format(self.e.SPOILER)
        if reason:
            msg = msg.prepend(f"{reason}: ")
        return msg
