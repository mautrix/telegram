# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2018 Tulir Asokan
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
import re
from html import escape, unescape
from html.parser import HTMLParser
from collections import deque
from telethon.tl.types import *
from . import user as u, puppet as p

log = None


class MatrixParser(HTMLParser):
    matrix_to_regex = re.compile("https://matrix.to/#/(@.+)")

    def __init__(self):
        super().__init__()
        self.text = ""
        self.entities = []
        self._building_entities = {}
        self._list_counter = 0
        self._open_tags = deque()
        self._open_tags_meta = deque()
        self._previous_ended_line = True

    def handle_starttag(self, tag, attrs):
        self._open_tags.appendleft(tag)
        self._open_tags_meta.appendleft(0)
        attrs = dict(attrs)
        EntityType = None
        args = {}
        if tag == "strong" or tag == "b":
            EntityType = MessageEntityBold
        elif tag == "em" or tag == "i":
            EntityType = MessageEntityItalic
        elif tag == "code":
            try:
                pre = self._building_entities["pre"]
                try:
                    pre.language = attrs["class"][len("language-"):]
                except KeyError:
                    pass
            except KeyError:
                EntityType = MessageEntityCode
        elif tag == "pre":
            EntityType = MessageEntityPre
            args["language"] = ""
        elif tag == "a":
            try:
                url = attrs["href"]
            except KeyError:
                return
            mention = self.matrix_to_regex.search(url)
            if mention:
                mxid = mention.group(1)
                puppet_match = p.Puppet.mxid_regex.search(mxid)
                if puppet_match:
                    user = p.Puppet.get(puppet_match.group(1), create=False)
                else:
                    user = u.User.get_by_mxid(mxid, create=False)
                if not user:
                    return
                if user.username:
                    EntityType = MessageEntityMention
                    url = f"@{user.username}"
                else:
                    EntityType = MessageEntityMentionName
                    args["user_id"] = user.tgid
            elif url.startswith("mailto:"):
                url = url[len("mailto:"):]
                EntityType = MessageEntityEmail
            else:
                if self.get_starttag_text() == url:
                    EntityType = MessageEntityUrl
                else:
                    EntityType = MessageEntityTextUrl
                    args["url"] = url
                    url = None
            self._open_tags_meta.popleft()
            self._open_tags_meta.appendleft(url)

        if EntityType and tag not in self._building_entities:
            self._building_entities[tag] = EntityType(offset=len(self.text), length=0, **args)

    def _list_depth(self):
        depth = 0
        for tag in self._open_tags:
            if tag == "ol" or tag == "ul":
                depth += 1
        return depth

    def handle_data(self, text):
        text = unescape(text)
        previous_tag = self._open_tags[0] if len(self._open_tags) > 0 else ""
        list_format_offset = 0
        if previous_tag == "a":
            url = self._open_tags_meta[0]
            if url:
                text = url
        elif len(self._open_tags) > 1 and self._previous_ended_line and previous_tag == "li":
            list_type = self._open_tags[1]
            indent = (self._list_depth() - 1) * 4 * " "
            text = text.strip("\n")
            if len(text) == 0:
                return
            elif list_type == "ul":
                text = f"{indent}* {text}"
                list_format_offset = len(indent) + 2
            elif list_type == "ol":
                n = self._open_tags_meta[1]
                n += 1
                self._open_tags_meta[1] = n
                text = f"{indent}{n}. {text}"
                list_format_offset = len(indent) + 3
        for tag, entity in self._building_entities.items():
            entity.length += len(text.strip("\n"))
            entity.offset += list_format_offset

        if text.endswith("\n"):
            self._previous_ended_line = True
        else:
            self._previous_ended_line = False

        self.text += text

    def handle_endtag(self, tag):
        try:
            self._open_tags.popleft()
            self._open_tags_meta.popleft()
        except IndexError:
            pass
        if (tag == "ul" or tag == "ol") and self.text.endswith("\n"):
            self.text = self.text[:-1]
        entity = self._building_entities.pop(tag, None)
        if entity:
            self.entities.append(entity)


def matrix_to_telegram(html):
    try:
        parser = MatrixParser()
        parser.feed(html)
        return parser.text, parser.entities
    except:
        log.exception("Failed to convert Matrix format:\nhtml=%s", html)


def telegram_to_matrix(text, entities):
    try:
        return _telegram_to_matrix(text, entities)
    except:
        log.exception("Failed to convert Telegram format:\n"
                      "message=%s\n"
                      "entities=%s",
                      text, entities)


def _telegram_to_matrix(text, entities):
    if not entities:
        return text
    html = []
    last_offset = 0
    for entity in entities:
        if entity.offset > last_offset:
            html.append(escape(text[last_offset:entity.offset]))
        elif entity.offset < last_offset:
            continue

        skip_entity = False
        entity_text = escape(text[entity.offset:entity.offset + entity.length])
        entity_type = type(entity)

        if entity_type == MessageEntityBold:
            html.append(f"<strong>{entity_text}</strong>")
        elif entity_type == MessageEntityItalic:
            html.append(f"<em>{entity_text}</em>")
        elif entity_type == MessageEntityCode:
            html.append(f"<code>{entity_text}</code>")
        elif entity_type == MessageEntityPre:
            if entity.language:
                html.append("<pre>"
                            f"<code class='language-{entity.language}'>{entity_text}</code>"
                            "</pre>")
            else:
                html.append(f"<pre><code>{entity_text}</code></pre>")
        elif entity_type == MessageEntityMention:
            username = entity_text[1:]

            user = u.User.find_by_username(username)
            if user:
                mxid = user.mxid
            else:
                puppet = p.Puppet.find_by_username(username)
                mxid = puppet.mxid if puppet else None
            if mxid:
                html.append(f"<a href='https://matrix.to/#/{mxid}'>{entity_text}</a>")
            else:
                skip_entity = True
        elif entity_type == MessageEntityMentionName:
            user = u.User.get_by_tgid(entity.user_id)
            if user:
                mxid = user.mxid
            else:
                puppet = p.Puppet.get(entity.user_id, create=False)
                mxid = puppet.mxid if puppet else None
            if mxid:
                html.append(f"<a href='https://matrix.to/#/{mxid}'>{entity_text}</a>")
            else:
                skip_entity = True
        elif entity_type == MessageEntityEmail:
            html.append(f"<a href='mailto:{entity_text}'>{entity_text}</a>")
        elif entity_type == MessageEntityUrl:
            html.append(f"<a href='{entity_text}'>{entity_text}</a>")
        elif entity_type == MessageEntityTextUrl:
            html.append(f"<a href='{escape(entity.url)}'>{entity_text}</a>")
        elif entity_type == MessageEntityBotCommand:
            html.append(f"<font color='blue'>!{entity_text[1:]}")
        elif entity_type == MessageEntityHashtag:
            html.append(f"<font color='blue'>{entity_text}</font>")
        else:
            skip_entity = True
        last_offset = entity.offset + (0 if skip_entity else entity.length)
    html.append(text[last_offset:])
    return "".join(html)


def init(context):
    global log
    _, _, parent_log, _ = context
    log = parent_log.getChild("formatter")
