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
from typing import List, Tuple, Pattern
import re

from telethon.tl.types import (MessageEntityMention as Mention, MessageEntityBotCommand as Command,
                               MessageEntityMentionName as MentionName, MessageEntityUrl as URL,
                               MessageEntityEmail as Email, MessageEntityTextUrl as TextURL,
                               MessageEntityBold as Bold, MessageEntityItalic as Italic,
                               MessageEntityCode as Code, MessageEntityPre as Pre,
                               MessageEntityStrike as Strike, MessageEntityUnderline as Underline,
                               MessageEntityBlockquote as Blockquote, TypeMessageEntity)

from ... import user as u, puppet as pu, portal as po
from ...types import MatrixUserID
from .telegram_message import TelegramMessage, Entity, offset_length_multiply

from .html_reader import HTMLNode, read_html

ParsedMessage = Tuple[str, List[TypeMessageEntity]]


def parse_html(input_html: str) -> ParsedMessage:
    return MatrixParser.parse(input_html)


class RecursionContext:
    def __init__(self, strip_linebreaks: bool = True, ul_depth: int = 0):
        self.strip_linebreaks = strip_linebreaks  # type: bool
        self.ul_depth = ul_depth  # type: int
        self._inited = True  # type: bool

    def __setattr__(self, key, value):
        if getattr(self, "_inited", False) is True:
            raise TypeError("'RecursionContext' object is immutable")
        super(RecursionContext, self).__setattr__(key, value)

    def enter_list(self) -> 'RecursionContext':
        return RecursionContext(strip_linebreaks=self.strip_linebreaks, ul_depth=self.ul_depth + 1)

    def enter_code_block(self) -> 'RecursionContext':
        return RecursionContext(strip_linebreaks=False, ul_depth=self.ul_depth)


class MatrixParser:
    mention_regex = re.compile("https://matrix.to/#/(@.+:.+)")  # type: Pattern
    room_regex = re.compile("https://matrix.to/#/(#.+:.+)")  # type: Pattern
    block_tags = ("p", "pre", "blockquote",
                  "ol", "ul", "li",
                  "h1", "h2", "h3", "h4", "h5", "h6",
                  "div", "hr", "table")  # type: Tuple[str, ...]
    list_bullets = ("●", "○", "■", "‣")  # type: Tuple[str, ...]

    @classmethod
    def list_bullet(cls, depth: int) -> str:
        return cls.list_bullets[(depth - 1) % len(cls.list_bullets)] + " "

    @classmethod
    def list_to_tmessage(cls, node: HTMLNode, ctx: RecursionContext) -> TelegramMessage:
        ordered = node.tag == "ol"
        tagged_children = cls.node_to_tagged_tmessages(node, ctx)
        counter = 1
        indent_length = 0
        if ordered:
            try:
                counter = int(node.attrib.get("start", "1"))
            except ValueError:
                counter = 1

            longest_index = counter - 1 + len(tagged_children)
            indent_length = len(str(longest_index))
        indent = (indent_length + 4) * " "
        children = []  # type: List[TelegramMessage]
        for child, tag in tagged_children:
            if tag != "li":
                continue

            if ordered:
                prefix = f"{counter}. "
                counter += 1
            else:
                prefix = cls.list_bullet(ctx.ul_depth)
            child = child.prepend(prefix)
            parts = child.split("\n")
            parts = parts[:1] + [part.prepend(indent) for part in parts[1:]]
            child = TelegramMessage.join(parts, "\n")
            children.append(child)
        return TelegramMessage.join(children, "\n")

    @classmethod
    def header_to_tmessage(cls, node: HTMLNode, ctx: RecursionContext) -> TelegramMessage:
        children = cls.node_to_tmessages(node, ctx)
        length = int(node.tag[1])
        prefix = "#" * length + " "
        return TelegramMessage.join(children, "").prepend(prefix).format(Bold)

    @classmethod
    def basic_format_to_tmessage(cls, node: HTMLNode, ctx: RecursionContext) -> TelegramMessage:
        msg = cls.tag_aware_parse_node(node, ctx)
        if node.tag in ("b", "strong"):
            msg.format(Bold)
        elif node.tag in ("i", "em"):
            msg.format(Italic)
        elif node.tag in ("s", "strike", "del"):
            msg.format(Strike)
        elif node.tag in ("u", "ins"):
            msg.format(Underline)
        elif node == "blockquote":
            msg.format(Blockquote)
        elif node.tag == "command":
            msg.format(Command)

        return msg

    @classmethod
    def link_to_tstring(cls, node: HTMLNode, ctx: RecursionContext) -> TelegramMessage:
        msg = cls.tag_aware_parse_node(node, ctx)
        href = node.attrib.get("href", "")
        if not href:
            return msg

        if href.startswith("mailto:"):
            return TelegramMessage(href[len("mailto:"):]).format(Email)

        mention = cls.mention_regex.match(href)
        if mention:
            mxid = MatrixUserID(mention.group(1))
            user = (pu.Puppet.get_by_mxid(mxid)
                    or u.User.get_by_mxid(mxid, create=False))
            if not user:
                return msg
            if user.username:
                return TelegramMessage(f"@{user.username}").format(Mention)
            elif user.tgid:
                displayname = user.plain_displayname or msg.text
                return TelegramMessage(displayname).format(MentionName, user_id=user.tgid)
            return msg

        room = cls.room_regex.match(href)
        if room:
            username = po.Portal.get_username_from_mx_alias(room.group(1))
            portal = po.Portal.find_by_username(username)
            if portal and portal.username:
                return TelegramMessage(f"@{portal.username}").format(Mention)

        return (msg.format(URL)
                if msg.text == href
                else msg.format(TextURL, url=href))

    @classmethod
    def blockquote_to_tmessage(cls, node: HTMLNode, ctx: RecursionContext) -> TelegramMessage:
        msg = cls.tag_aware_parse_node(node, ctx)
        children = msg.trim().split("\n")
        children = [child.prepend("> ") for child in children]
        return TelegramMessage.join(children, "\n")

    @classmethod
    def node_to_tmessage(cls, node: HTMLNode, ctx: RecursionContext) -> TelegramMessage:
        if node.tag == "ol":
            return cls.list_to_tmessage(node, ctx)
        elif node.tag == "ul":
            return cls.list_to_tmessage(node, ctx.enter_list())
        elif node.tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            return cls.header_to_tmessage(node, ctx)
        elif node.tag == "br":
            return TelegramMessage("\n")
        elif node.tag in ("b", "strong", "i", "em", "s", "del", "u", "ins", "command"):
            return cls.basic_format_to_tmessage(node, ctx)
        elif node.tag == "blockquote":
            # Telegram already has blockquote entities in the protocol schema, but it strips them
            # server-side and none of the official clients support them.
            # TODO once Telegram changes that, use the above if block for blockquotes too.
            return cls.blockquote_to_tmessage(node, ctx)
        elif node.tag == "a":
            return cls.link_to_tstring(node, ctx)
        elif node.tag == "p":
            return cls.tag_aware_parse_node(node, ctx).append("\n")
        elif node.tag == "pre":
            lang = ""
            try:
                if node[0].tag == "code":
                    node = node[0]
                    lang = node.attrib["class"][len("language-"):]
            except (IndexError, KeyError):
                pass
            return cls.parse_node(node, ctx.enter_code_block()).format(Pre, language=lang)
        elif node.tag == "code":
            return cls.parse_node(node, ctx.enter_code_block()).format(Code)
        return cls.tag_aware_parse_node(node, ctx)

    @staticmethod
    def text_to_tmessage(text: str, ctx: RecursionContext) -> TelegramMessage:
        if ctx.strip_linebreaks:
            text = text.replace("\n", "")
        return TelegramMessage(text)

    @classmethod
    def node_to_tagged_tmessages(cls, node: HTMLNode, ctx: RecursionContext
                                 ) -> List[Tuple[TelegramMessage, str]]:
        output = []

        if node.text:
            output.append((cls.text_to_tmessage(node.text, ctx), "text"))
        for child in node:
            output.append((cls.node_to_tmessage(child, ctx), child.tag))
            if child.tail:
                output.append((cls.text_to_tmessage(child.tail, ctx), "text"))
        return output

    @classmethod
    def node_to_tmessages(cls, node: HTMLNode, ctx: RecursionContext
                          ) -> List[TelegramMessage]:
        return [msg for (msg, tag) in cls.node_to_tagged_tmessages(node, ctx)]

    @classmethod
    def tag_aware_parse_node(cls, node: HTMLNode, ctx: RecursionContext
                             ) -> TelegramMessage:
        msgs = cls.node_to_tagged_tmessages(node, ctx)
        output = TelegramMessage()
        prev_was_block = False
        for msg, tag in msgs:
            if tag in cls.block_tags:
                msg = msg.append("\n")
                if not prev_was_block:
                    msg = msg.prepend("\n")
                prev_was_block = True
            output = output.append(msg)
        return output.trim()

    @classmethod
    def parse_node(cls, node: HTMLNode, ctx: RecursionContext) -> TelegramMessage:
        return TelegramMessage.join(cls.node_to_tmessages(node, ctx))

    @classmethod
    def parse(cls, data: str) -> ParsedMessage:
        msg = cls.node_to_tmessage(read_html(f"<body>{data}</body>"), RecursionContext())
        return msg.text, msg.entities
