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
from typing import List, Tuple, Optional

from telethon.tl.types import TypeMessageEntity

from mautrix.types import UserID, RoomID
from mautrix.util.formatter import MatrixParser as BaseMatrixParser, RecursionContext
from mautrix.util.formatter.html_reader_htmlparser import read_html, HTMLNode

from ... import user as u, puppet as pu, portal as po
from .telegram_message import TelegramMessage, TelegramEntityType


ParsedMessage = Tuple[str, List[TypeMessageEntity]]


def parse_html(input_html: str) -> ParsedMessage:
    msg = MatrixParser.parse(input_html)
    return msg.text, msg.telegram_entities


class MatrixParser(BaseMatrixParser[TelegramMessage]):
    e = TelegramEntityType
    fs = TelegramMessage
    read_html = read_html

    @classmethod
    def custom_node_to_fstring(cls, node: HTMLNode, ctx: RecursionContext
                               ) -> Optional[TelegramMessage]:
        msg = cls.tag_aware_parse_node(node, ctx)
        if node.tag == "command":
            msg.format(TelegramEntityType.COMMAND)
        return None

    @classmethod
    def user_pill_to_fstring(cls, msg: TelegramMessage, user_id: UserID) -> TelegramMessage:
        user = (pu.Puppet.deprecated_sync_get_by_mxid(user_id)
                or u.User.get_by_mxid(user_id, create=False))
        if not user:
            return msg
        if user.username:
            return TelegramMessage(f"@{user.username}").format(TelegramEntityType.MENTION)
        elif user.tgid:
            displayname = user.plain_displayname or msg.text
            return TelegramMessage(displayname).format(TelegramEntityType.MENTION_NAME,
                                                       user_id=user.tgid)
        return msg

    @classmethod
    def url_to_fstring(cls, msg: TelegramMessage, url: str) -> TelegramMessage:
        if url == msg.text:
            return msg.format(cls.e.URL)
        else:
            return msg.format(cls.e.INLINE_URL, url=url)

    @classmethod
    def room_pill_to_fstring(cls, msg: TelegramMessage, room_id: RoomID) -> TelegramMessage:
        username = po.Portal.get_username_from_mx_alias(room_id)
        portal = po.Portal.find_by_username(username)
        if portal and portal.username:
            return TelegramMessage(f"@{portal.username}").format(TelegramEntityType.MENTION)

    @classmethod
    def header_to_fstring(cls, node: HTMLNode, ctx: RecursionContext) -> TelegramMessage:
        children = cls.node_to_fstrings(node, ctx)
        length = int(node.tag[1])
        prefix = "#" * length + " "
        return TelegramMessage.join(children, "").prepend(prefix).format(TelegramEntityType.BOLD)

    @classmethod
    def blockquote_to_fstring(cls, node: HTMLNode, ctx: RecursionContext) -> TelegramMessage:
        msg = cls.tag_aware_parse_node(node, ctx)
        children = msg.trim().split("\n")
        children = [child.prepend("> ") for child in children]
        return TelegramMessage.join(children, "\n")
