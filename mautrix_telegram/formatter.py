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
from telethon.tl.types import *
from . import user as u, puppet as p


def telegram_to_matrix(text, entities):
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
