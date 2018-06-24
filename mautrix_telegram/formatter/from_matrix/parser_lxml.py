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
from lxml import etree
from typing import Optional, List, Tuple, Type, Callable, Dict, Any
import math
import re
import logging

from telethon.tl.types import (MessageEntityMention, MessageEntityMentionName, MessageEntityEmail,
                               MessageEntityUrl, MessageEntityTextUrl, MessageEntityBold,
                               MessageEntityItalic, MessageEntityCode, MessageEntityPre,
                               MessageEntityBotCommand, TypeMessageEntity)

from ...context import Context
from ... import user as u, puppet as pu, portal as po
from ...db import Message as DBMessage
from ...formatter.util import (add_surrogates, remove_surrogates, trim_reply_fallback_html,
                               trim_reply_fallback_text, html_to_unicode)
from .parser_common import MatrixParserCommon, ParsedMessage


class MatrixParser(MatrixParserCommon):
    def __init__(self):
        self.text = ""
        self.entities = []

    def parse_node(self, node) -> ParsedMessage:
        pass

    def feed(self, html: str):
        document = etree.parse(html)
        self.text, self.entities = self.parse_node(document)


def parse_html(html: str) -> ParsedMessage:
    parser = MatrixParser()
    parser.feed(html)
    return parser.text, parser.entities
