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
from html import escape
try:
    from lxml.html.diff import htmldiff
except ImportError:
    htmldiff = None
import logging
import re

from telethon_aio.tl.types import *
from mautrix_appservice import MatrixRequestError

from .. import user as u, puppet as pu, portal as po
from ..db import Message as DBMessage
from .util import (add_surrogates, remove_surrogates, trim_reply_fallback_html,
                   trim_reply_fallback_text, unicode_to_html)

log = logging.getLogger("mau.fmt.tg")
should_highlight_edits = False


def telegram_reply_to_matrix(evt, source):
    if evt.reply_to_msg_id:
        space = (evt.to_id.channel_id
                 if isinstance(evt, Message) and isinstance(evt.to_id, PeerChannel)
                 else source.tgid)
        msg = DBMessage.query.get((evt.reply_to_msg_id, space))
        if msg:
            return {
                "m.in_reply_to": {
                    "event_id": msg.mxid,
                    "room_id": msg.mx_room,
                }
            }
    return {}


async def _add_forward_header(source, text, html, fwd_from_id):
    if not html:
        html = escape(text)
    user = u.User.get_by_tgid(fwd_from_id)
    if user:
        fwd_from = f"<a href='https://matrix.to/#/{user.mxid}'>{user.mxid}</a>"
    else:
        puppet = pu.Puppet.get(fwd_from_id, create=False)
        if puppet and puppet.displayname:
            fwd_from = f"<a href='https://matrix.to/#/{puppet.mxid}'>{puppet.displayname}</a>"
        else:
            user = await source.client.get_entity(fwd_from_id)
            if user:
                fwd_from = f"<b>{pu.Puppet.get_displayname(user, format=False)}</b>"
            else:
                fwd_from = None
    if not fwd_from:
        fwd_from = "<b>Unknown user</b>"
    text = f"Forwarded from {fwd_from}:\n{text}"
    html = (f"Forwarded message from {fwd_from}<br/>"
            f"<blockquote>{html}</blockquote>")
    return text, html


def highlight_edits(new_html, old_html):
    # Don't include `Edit:` text in diff.
    if old_html.startswith("<u>Edit:</u> "):
        old_html = old_html[len("<u>Edit:</u> "):]

    # Generate diff with lxml
    new_html = htmldiff(old_html, new_html)

    # Replace <ins> with <u> since Riot doesn't allow <ins>
    new_html = new_html.replace("<ins>", "<u>").replace("</ins>", "</u>")
    # Remove <del>s since we just want to hide deletions.
    new_html = re.sub("<del>.+?</del>", "", new_html)
    return new_html


async def _add_reply_header(source, text, html, evt, relates_to, main_intent, is_edit):
    space = (evt.to_id.channel_id
             if isinstance(evt, Message) and isinstance(evt.to_id, PeerChannel)
             else source.tgid)

    msg = DBMessage.query.get((evt.reply_to_msg_id, space))
    if not msg:
        return text, html

    relates_to["m.in_reply_to"] = {
        "event_id": msg.mxid,
        "room_id": msg.mx_room,
    }

    try:
        event = await main_intent.get_event(msg.mx_room, msg.mxid)

        content = event["content"]
        r_sender = event["sender"]

        r_text_body = trim_reply_fallback_text(content["body"])
        r_html_body = trim_reply_fallback_html(content["formatted_body"]
                                               if "formatted_body" in content
                                               else escape(content["body"]))

        puppet = pu.Puppet.get_by_mxid(r_sender, create=False)
        r_displayname = puppet.displayname if puppet else r_sender
        r_sender_link = f"<a href='https://matrix.to/#/{r_sender}'>{r_displayname}</a>"

        if is_edit and should_highlight_edits:
            html = highlight_edits(html or escape(text), r_html_body)
    except (ValueError, KeyError, MatrixRequestError):
        r_sender_link = "unknown user"
        # r_sender = "unknown user"
        r_text_body = "Failed to fetch message"
        r_html_body = "<em>Failed to fetch message</em>"

    if is_edit:
        html = f"<u>Edit:</u> {html or escape(text)}"
        text = f"Edit: {text}"

    r_keyword = "In reply to" if not is_edit else "Edit to"
    r_msg_link = f"<a href='https://matrix.to/#/{msg.mx_room}/{msg.mxid}'>{r_keyword}</a>"
    html = (f"<blockquote data-mx-reply>{r_msg_link} {r_sender_link} {r_html_body}</blockquote>"
            + (html or escape(text)))

    lines = r_text_body.strip().split("\n")
    text_with_quote = f"> <{r_displayname}> {lines.pop(0)}"
    for line in lines:
        if line:
            text_with_quote += f"\n> {line}"
    text_with_quote += "\n\n"
    text_with_quote += text
    return text_with_quote, html


async def telegram_to_matrix(evt, source, main_intent=None, is_edit=False, prefix_text=None,
                             prefix_html=None):
    text = add_surrogates(evt.message)
    html = _telegram_entities_to_matrix_catch(text, evt.entities) if evt.entities else None
    relates_to = {}

    if prefix_html:
        html = prefix_html + (html or escape(text))
    if prefix_text:
        text = prefix_text + text

    if evt.fwd_from:
        text, html = await _add_forward_header(source, text, html, evt.fwd_from.from_id)

    if evt.reply_to_msg_id:
        text, html = await _add_reply_header(source, text, html, evt, relates_to, main_intent,
                                             is_edit)

    if isinstance(evt, Message) and evt.post and evt.post_author:
        if not html:
            html = escape(text)
        text += f"\n- {evt.post_author}"
        html += f"<br/><i>- <u>{evt.post_author}</u></i>"

    html = unicode_to_html(text, html, "\u0336", "del")
    html = unicode_to_html(text, html, "\u0332", "u")

    if html:
        html = html.replace("\n", "<br/>")

    return remove_surrogates(text), remove_surrogates(html), relates_to


def _telegram_entities_to_matrix_catch(text, entities):
    try:
        return _telegram_entities_to_matrix(text, entities)
    except Exception:
        log.exception("Failed to convert Telegram format:\n"
                      "message=%s\n"
                      "entities=%s",
                      text, entities)


def _telegram_entities_to_matrix(text, entities):
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
            skip_entity = _parse_pre(html, entity_text, entity.language)
        elif entity_type == MessageEntityMention:
            skip_entity = _parse_mention(html, entity_text)
        elif entity_type == MessageEntityMentionName:
            skip_entity = _parse_name_mention(html, entity_text, entity.user_id)
        elif entity_type == MessageEntityEmail:
            html.append(f"<a href='mailto:{entity_text}'>{entity_text}</a>")
        elif entity_type in {MessageEntityTextUrl, MessageEntityUrl}:
            skip_entity = _parse_url(html, entity_text,
                                     entity.url if entity_type == MessageEntityTextUrl else None)
        elif entity_type == MessageEntityBotCommand:
            html.append(f"<font color='blue'>!{entity_text[1:]}</font>")
        elif entity_type == MessageEntityHashtag:
            html.append(f"<font color='blue'>{entity_text}</font>")
        else:
            skip_entity = True
        last_offset = entity.offset + (0 if skip_entity else entity.length)
    html.append(text[last_offset:])

    return "".join(html)


def _parse_pre(html, entity_text, language):
    if language:
        html.append("<pre>"
                    f"<code class='language-{language}'>{entity_text}</code>"
                    "</pre>")
    else:
        html.append(f"<pre><code>{entity_text}</code></pre>")
    return False


def _parse_mention(html, entity_text):
    username = entity_text[1:]

    user = u.User.find_by_username(username) or pu.Puppet.find_by_username(username)
    if user:
        mxid = user.mxid
    else:
        portal = po.Portal.find_by_username(username)
        mxid = portal.alias or portal.mxid if portal else None

    if mxid:
        html.append(f"<a href='https://matrix.to/#/{mxid}'>{entity_text}</a>")
    else:
        return True
    return False


def _parse_name_mention(html, entity_text, user_id):
    user = u.User.get_by_tgid(user_id)
    if user:
        mxid = user.mxid
    else:
        puppet = pu.Puppet.get(user_id, create=False)
        mxid = puppet.mxid if puppet else None
    if mxid:
        html.append(f"<a href='https://matrix.to/#/{mxid}'>{entity_text}</a>")
    else:
        return True
    return False


def _parse_url(html, entity_text, url):
    url = escape(url) if url else entity_text
    if not url.startswith(("https://", "http://", "ftp://", "magnet://")):
        url = "http://" + url
    html.append(f"<a href='{url}'>{entity_text}</a>")
    return False


def init_tg(context):
    global should_highlight_edits
    should_highlight_edits = htmldiff and context.config["bridge.highlight_edits"]
