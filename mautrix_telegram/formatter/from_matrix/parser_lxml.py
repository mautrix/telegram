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
from typing import Optional, List, Tuple, Union, Callable
from lxml import html

from telethon.tl.types import (MessageEntityMention as Mention,
                               MessageEntityMentionName as MentionName, MessageEntityEmail as Email,
                               MessageEntityUrl as URL, MessageEntityTextUrl as TextURL,
                               MessageEntityBold as Bold, MessageEntityItalic as Italic,
                               MessageEntityCode as Code, MessageEntityPre as Pre,
                               MessageEntityBotCommand as Command, TypeMessageEntity,
                               InputMessageEntityMentionName as InputMentionName)

from ... import user as u, puppet as pu, portal as po
from ..util import html_to_unicode
from .parser_common import MatrixParserCommon, ParsedMessage


def parse_html(html: str) -> ParsedMessage:
    return MatrixParser.parse(html)


class Entity:
    @staticmethod
    def copy(entity: TypeMessageEntity) -> Optional[TypeMessageEntity]:
        if not entity:
            return None
        kwargs = {
            "offset": entity.offset,
            "length": entity.length,
        }
        if isinstance(entity, Pre):
            kwargs["language"] = entity.language
        elif isinstance(entity, TextURL):
            kwargs["url"] = entity.url
        elif isinstance(entity, (MentionName, InputMentionName)):
            kwargs["user_id"] = entity.user_id
        return entity.__class__(**kwargs)

    @classmethod
    def adjust(cls, entity: Union[TypeMessageEntity, List[TypeMessageEntity]],
               func: Callable[[TypeMessageEntity], None]
               ) -> Union[Optional[TypeMessageEntity], List[TypeMessageEntity]]:
        if isinstance(entity, list):
            return [Entity.adjust(element, func) for element in entity if entity]
        elif not entity:
            return None
        entity = cls.copy(entity)
        func(entity)
        if entity.offset < 0:
            entity.length += entity.offset
            entity.offset = 0
        return entity


def offset_diff(amount: int):
    def func(entity: TypeMessageEntity):
        entity.offset += amount

    return func


def offset_length_multiply(amount: int):
    def func(entity: TypeMessageEntity):
        entity.offset *= amount
        entity.length *= amount

    return func


class TelegramMessage:
    def __init__(self, text: str = "", entities: Optional[List[TypeMessageEntity]] = None):
        self.text = text  # type: str
        self.entities = entities or []  # type: List[TypeMessageEntity]

    def offset_entities(self, offset: int) -> "TelegramMessage":
        def apply_offset(entity: TypeMessageEntity, inner_offset: int
                         ) -> Optional[TypeMessageEntity]:
            entity = Entity.copy(entity)
            entity.offset += inner_offset
            if entity.offset < 0:
                entity.offset = 0
            elif entity.offset > len(self.text):
                return None
            elif entity.offset + entity.length > len(self.text):
                entity.length = len(self.text) - entity.offset
            return entity

        self.entities = [apply_offset(entity, offset) for entity in self.entities if entity]
        self.entities = [x for x in self.entities if x is not None]
        return self

    def append(self, *args: Union[str, "TelegramMessage"]) -> "TelegramMessage":
        for msg in args:
            if isinstance(msg, str):
                msg = TelegramMessage(text=msg)
            self.entities += Entity.adjust(msg.entities, offset_diff(len(self.text)))
            self.text += msg.text
        return self

    def prepend(self, *args: Union[str, "TelegramMessage"]) -> "TelegramMessage":
        for msg in args:
            if isinstance(msg, str):
                msg = TelegramMessage(text=msg)
            self.entities = msg.entities + Entity.adjust(self.entities, offset_diff(len(msg.text)))
            self.text = msg.text + self.text
        return self

    def format(self, entity_type: type(TypeMessageEntity), offset: int = None, length: int = None,
               **kwargs) -> "TelegramMessage":
        self.entities.append(entity_type(offset=offset or 0,
                                         length=length if length is not None else len(self.text),
                                         **kwargs))
        return self

    def concat(self, *args: Union[str, "TelegramMessage"]) -> "TelegramMessage":
        return TelegramMessage().append(self, *args)

    def trim(self) -> "TelegramMessage":
        orig_len = len(self.text)
        self.text = self.text.lstrip()
        diff = orig_len - len(self.text)
        self.text = self.text.rstrip()
        self.offset_entities(-diff)
        return self

    def split(self, separator, max_items: int = 0) -> List["TelegramMessage"]:
        text_parts = self.text.split(separator, max_items - 1)
        output = []  # type: List[TelegramMessage]

        offset = 0
        for part in text_parts:
            msg = TelegramMessage(part)
            for entity in self.entities:
                start_in_range = len(part) > entity.offset - offset >= 0
                end_in_range = len(part) >= entity.offset - offset + entity.length > 0
                if start_in_range and end_in_range:
                    msg.entities.append(Entity.adjust(entity, offset_diff(-offset)))
            output.append(msg)

            offset += len(part)
            offset += len(separator)

        return output

    @staticmethod
    def join(items: List[Union[str, "TelegramMessage"]], separator: str = " ") -> "TelegramMessage":
        main = TelegramMessage()
        for msg in items:
            if isinstance(msg, str):
                msg = TelegramMessage(text=msg)
            main.entities += Entity.adjust(msg.entities, offset_diff(len(main.text)))
            main.text += msg.text + separator
        main.text = main.text[:-len(separator)]
        return main


class MatrixParser(MatrixParserCommon):
    @classmethod
    def list_to_tmessage(cls, node: html.HtmlElement, strip_linebreaks) -> TelegramMessage:
        ordered = node.tag == "ol"
        tagged_children = cls.node_to_tagged_tmessages(node, strip_linebreaks)
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
                prefix = "● "
            child = child.prepend(prefix)
            parts = child.split("\n")
            parts = parts[:1] + [part.prepend(indent) for part in parts[1:]]
            child = TelegramMessage.join(parts, "\n")
            children.append(child)
        return TelegramMessage.join(children, "\n")

    @classmethod
    def blockquote_to_tmessage(cls, node: html.HtmlElement, strip_linebreaks) -> TelegramMessage:
        msg = cls.tag_aware_parse_node(node, strip_linebreaks)
        children = msg.trim().split("\n")
        children = [child.prepend("> ") for child in children]
        return TelegramMessage.join(children, "\n")

    @classmethod
    def header_to_tmessage(cls, node: html.HtmlElement, strip_linebreaks) -> TelegramMessage:
        children = cls.node_to_tmessages(node, strip_linebreaks)
        length = int(node.tag[1])
        prefix = "#" * length + " "
        return TelegramMessage.join(children, "").prepend(prefix)

    @classmethod
    def basic_format_to_tmessage(cls, node: html.HtmlElement, strip_linebreaks) -> TelegramMessage:
        msg = cls.tag_aware_parse_node(node, strip_linebreaks)
        if node.tag in ("b", "strong"):
            msg.format(Bold)
        elif node.tag in ("i", "em"):
            msg.format(Italic)
        elif node.tag == "command":
            msg.format(Command)
        elif node.tag in ("s", "del"):
            msg.text = html_to_unicode(msg.text, "\u0336")
        elif node.tag in ("u", "ins"):
            msg.text = html_to_unicode(msg.text, "\u0332")

        if node.tag in ("s", "del", "u", "ins"):
            msg.entities = Entity.adjust(msg.entities, offset_length_multiply(2))

        return msg

    @classmethod
    def link_to_tstring(cls, node: html.HtmlElement, strip_linebreaks) -> TelegramMessage:
        msg = cls.tag_aware_parse_node(node, strip_linebreaks)
        href = node.attrib.get("href", "")
        if not href:
            return msg

        if href.startswith("mailto:"):
            return TelegramMessage(href[len("mailto:"):]).format(Email)

        mention = cls.mention_regex.match(href)
        if mention:
            mxid = mention.group(1)
            user = (pu.Puppet.get_by_mxid(mxid)
                    or u.User.get_by_mxid(mxid, create=False))
            if not user:
                return msg
            if user.username:
                return TelegramMessage(f"@{user.username}").format(Mention)
            elif user.tgid:
                return TelegramMessage(user.displayname or msg.text).format(MentionName,
                                                                            user_id=user.tgid)
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
    def node_to_tmessage(cls, node: html.HtmlElement, strip_linebreaks) -> TelegramMessage:
        if node.tag == "blockquote":
            return cls.blockquote_to_tmessage(node, strip_linebreaks)
        elif node.tag in ("ol", "ul"):
            return cls.list_to_tmessage(node, strip_linebreaks)
        elif node.tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            return cls.header_to_tmessage(node, strip_linebreaks)
        elif node.tag == "br":
            return TelegramMessage("\n")
        elif node.tag in ("b", "strong", "i", "em", "s", "del", "u", "ins", "command"):
            return cls.basic_format_to_tmessage(node, strip_linebreaks)
        elif node.tag == "a":
            return cls.link_to_tstring(node, strip_linebreaks)
        elif node.tag == "p":
            return cls.tag_aware_parse_node(node, strip_linebreaks).append("\n")
        elif node.tag == "pre":
            lang = ""
            try:
                if node[0].tag == "code":
                    lang = node[0].attrib["class"][len("language-"):]
                    node = node[0]
            except (IndexError, KeyError):
                pass
            return cls.parse_node(node, strip_linebreaks=False).format(Pre, language=lang)
        elif node.tag == "code":
            return cls.parse_node(node, strip_linebreaks=False).format(Code)
        return cls.tag_aware_parse_node(node, strip_linebreaks)

    @staticmethod
    def text_to_tmessage(text: str, strip_linebreaks: bool = True) -> TelegramMessage:
        if strip_linebreaks:
            text = text.replace("\n", "")
        return TelegramMessage(text)

    @classmethod
    def node_to_tagged_tmessages(cls, node: html.HtmlElement, strip_linebreaks: bool = True
                                 ) -> List[Tuple[TelegramMessage, str]]:
        output = []

        if node.text:
            output.append((cls.text_to_tmessage(node.text, strip_linebreaks), "text"))
        for child in node:
            output.append((cls.node_to_tmessage(child, strip_linebreaks), child.tag))
            if child.tail:
                output.append((cls.text_to_tmessage(child.tail, strip_linebreaks), "text"))
        return output

    @classmethod
    def node_to_tmessages(cls, node: html.HtmlElement, strip_linebreaks) -> List[TelegramMessage]:
        return [msg for (msg, tag) in cls.node_to_tagged_tmessages(node, strip_linebreaks)]

    @classmethod
    def tag_aware_parse_node(cls, node: html.HtmlElement, strip_linebreaks) -> TelegramMessage:
        msgs = cls.node_to_tagged_tmessages(node, strip_linebreaks)
        output = TelegramMessage()
        for msg, tag in msgs:
            if tag in cls.block_tags:
                msg = msg.append("\n").prepend("\n")
            output = output.append(msg)
        return output.trim()

    @classmethod
    def parse_node(cls, node: html.HtmlElement, strip_linebreaks) -> TelegramMessage:
        return TelegramMessage.join(cls.node_to_tmessages(node, strip_linebreaks))

    @classmethod
    def parse(cls, data: str) -> ParsedMessage:
        document = html.fromstring(f"<html>{data}</html>")
        msg = cls.parse_node(document, strip_linebreaks=True)
        return msg.text, msg.entities
