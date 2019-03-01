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
from typing import Dict, List, Tuple

from html.parser import HTMLParser


class HTMLNode(list):
    def __init__(self, tag: str, attrs: List[Tuple[str, str]]):
        super().__init__()
        self.tag = tag  # type: str
        self.text = ""  # type: str
        self.tail = ""  # type: str
        self.attrib = dict(attrs)  # type: Dict[str, str]


class NodeifyingParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = [HTMLNode("html", [])]  # type: List[HTMLNode]

    def handle_starttag(self, tag, attrs):
        node = HTMLNode(tag, attrs)
        self.stack[-1].append(node)
        self.stack.append(node)

    def handle_endtag(self, tag):
        if tag == self.stack[-1].tag:
            self.stack.pop()

    def handle_data(self, data):
        if len(self.stack[-1]) > 0:
            self.stack[-1][-1].tail += data
        else:
            self.stack[-1].text += data

    def error(self, message):
        pass


def read_html(data: str) -> HTMLNode:
    parser = NodeifyingParser()
    parser.feed(data)
    return parser.stack[0]
