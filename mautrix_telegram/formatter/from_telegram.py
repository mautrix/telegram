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
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING
from html import escape
import logging
import re

from telethon.tl.types import (MessageEntityMention, MessageEntityMentionName, MessageEntityUrl,
                               MessageEntityEmail, MessageEntityTextUrl, MessageEntityBold,
                               MessageEntityItalic, MessageEntityCode, MessageEntityPre,
                               MessageEntityBotCommand, MessageEntityHashtag, MessageEntityCashtag,
                               MessageEntityPhone, TypeMessageEntity, Message, PeerChannel,
                               MessageFwdHeader, PeerUser)

from mautrix_appservice import MatrixRequestError
from mautrix_appservice.intent_api import IntentAPI

from .. import user as u, puppet as pu, portal as po
from ..types import TelegramID
from ..db import Message as DBMessage
from .util import (add_surrogates, remove_surrogates, trim_reply_fallback_html,
                   trim_reply_fallback_text, unicode_to_html)

if TYPE_CHECKING:
    from ..abstract_user import AbstractUser
    from ..context import Context

try:
    from lxml.html.diff import htmldiff
except ImportError:
    htmldiff = None  # type: ignore


log = logging.getLogger("mau.fmt.tg")  # type: logging.Logger
should_highlight_edits = False  # type: bool


def telegram_reply_to_matrix(evt: Message, source: 'AbstractUser') -> Dict:
    if evt.reply_to_msg_id:
        space = (evt.to_id.channel_id
                 if isinstance(evt, Message) and isinstance(evt.to_id, PeerChannel)
                 else source.tgid)
        msg = DBMessage.get_by_tgid(evt.reply_to_msg_id, space)
        if msg:
            return {
                "m.in_reply_to": {
                    "event_id": msg.mxid,
                    "room_id": msg.mx_room,
                }
            }
    return {}


async def _add_forward_header(source, text: str, html: Optional[str],
                              fwd_from: MessageFwdHeader) -> Tuple[str, str]:
    if not html:
        html = escape(text)
    fwd_from_html, fwd_from_text = None, None
    if fwd_from.from_id:
        user = u.User.get_by_tgid(fwd_from.from_id)
        if user:
            fwd_from_text = user.displayname or user.mxid
            fwd_from_html = f"<a href='https://matrix.to/#/{user.mxid}'>{fwd_from_text}</a>"

        if not fwd_from_text:
            puppet = pu.Puppet.get(TelegramID(fwd_from.from_id), create=False)
            if puppet and puppet.displayname:
                fwd_from_text = puppet.displayname or puppet.mxid
                fwd_from_html = f"<a href='https://matrix.to/#/{puppet.mxid}'>{fwd_from_text}</a>"

        if not fwd_from_text:
            user = await source.client.get_entity(PeerUser(fwd_from.from_id))
            if user:
                fwd_from_text = pu.Puppet.get_displayname(user, False)
                fwd_from_html = f"<b>{fwd_from_text}</b>"

    if not fwd_from_text:
        if fwd_from.from_id:
            fwd_from_text = "Unknown user"
        else:
            fwd_from_text = "Unknown source"
        fwd_from_html = f"<b>{fwd_from_text}</b>"

    text = "\n".join([f"> {line}" for line in text.split("\n")])
    text = f"Forwarded from {fwd_from_text}:\n{text}"
    html = (f"Forwarded message from {fwd_from_html}<br/>"
            f"<tg-forward><blockquote>{html}</blockquote></tg-forward>")
    return text, html


def highlight_edits(new_html: str, old_html: str) -> str:
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


async def _add_reply_header(source: "AbstractUser", text: str, html: str, evt: Message,
                            relates_to: Dict, main_intent: IntentAPI, is_edit: bool
                            ) -> Tuple[str, str]:
    space = (evt.to_id.channel_id
             if isinstance(evt, Message) and isinstance(evt.to_id, PeerChannel)
             else source.tgid)

    msg = DBMessage.get_by_tgid(evt.reply_to_msg_id, space)
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
        r_displayname = "unknown user"
        r_text_body = "Failed to fetch message"
        r_html_body = "<em>Failed to fetch message</em>"

    if is_edit:
        html = f"<u>Edit:</u> {html or escape(text)}"
        text = f"Edit: {text}"

    r_keyword = "In reply to" if not is_edit else "Edit to"
    r_msg_link = f"<a href='https://matrix.to/#/{msg.mx_room}/{msg.mxid}'>{r_keyword}</a>"
    html = (
        f"<mx-reply><blockquote>{r_msg_link} {r_sender_link}\n{r_html_body}</blockquote></mx-reply>"
        + (html or escape(text)))

    lines = r_text_body.strip().split("\n")
    text_with_quote = f"> <{r_displayname}> {lines.pop(0)}"
    for line in lines:
        if line:
            text_with_quote += f"\n> {line}"
    text_with_quote += "\n\n"
    text_with_quote += text
    return text_with_quote, html


async def telegram_to_matrix(evt: Message, source: "AbstractUser",
                             main_intent: Optional[IntentAPI] = None,
                             is_edit: bool = False, prefix_text: Optional[str] = None,
                             prefix_html: Optional[str] = None) -> Tuple[str, str, Dict]:
    text = add_surrogates(evt.message)
    html = _telegram_entities_to_matrix_catch(text, evt.entities) if evt.entities else None
    relates_to = {}  # type: Dict

    if prefix_html:
        html = prefix_html + (html or escape(text))
    if prefix_text:
        text = prefix_text + text

    if evt.fwd_from:
        text, html = await _add_forward_header(source, text, html, evt.fwd_from)

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


def _telegram_entities_to_matrix_catch(text: str, entities: List[TypeMessageEntity]) -> str:
    try:
        return _telegram_entities_to_matrix(text, entities)
    except Exception:
        log.exception("Failed to convert Telegram format:\n"
                      "message=%s\n"
                      "entities=%s",
                      text, entities)
    return "[failed conversion in _telegram_entities_to_matrix]"


def _telegram_entities_to_matrix(text: str, entities: List[TypeMessageEntity]) -> str:
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
            html.append(f"<pre><code>{entity_text}</code></pre>"
                        if "\n" in entity_text
                        else f"<code>{entity_text}</code>")
        elif entity_type == MessageEntityPre:
            skip_entity = _parse_pre(html, entity_text, entity.language)
        elif entity_type == MessageEntityMention:
            skip_entity = _parse_mention(html, entity_text)
        elif entity_type == MessageEntityMentionName:
            skip_entity = _parse_name_mention(html, entity_text, TelegramID(entity.user_id))
        elif entity_type == MessageEntityEmail:
            html.append(f"<a href='mailto:{entity_text}'>{entity_text}</a>")
        elif entity_type in (MessageEntityTextUrl, MessageEntityUrl):
            skip_entity = _parse_url(html, entity_text,
                                     entity.url if entity_type == MessageEntityTextUrl else None)
        elif entity_type == MessageEntityBotCommand:
            html.append(f"<font color='blue'>!{entity_text[1:]}</font>")
        elif entity_type in (MessageEntityHashtag, MessageEntityCashtag, MessageEntityPhone):
            html.append(f"<font color='blue'>{entity_text}</font>")
        else:
            skip_entity = True
        last_offset = entity.offset + (0 if skip_entity else entity.length)
    html.append(text[last_offset:])

    return "".join(html)


def _parse_pre(html: List[str], entity_text: str, language: str) -> bool:
    if language:
        html.append("<pre>"
                    f"<code class='language-{language}'>{entity_text}</code>"
                    "</pre>")
    else:
        html.append(f"<pre><code>{entity_text}</code></pre>")
    return False


def _parse_mention(html: List[str], entity_text: str) -> bool:
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


def _parse_name_mention(html: List[str], entity_text: str, user_id: TelegramID) -> bool:
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


message_link_regex = re.compile(
    r"https?://t(?:elegram)?\.(?:me|dog)/([A-Za-z][A-Za-z0-9_]{3,}[A-Za-z0-9])/([0-9]{1,50})")


def _parse_url(html: List[str], entity_text: str, url: str) -> bool:
    url = escape(url) if url else entity_text
    if not url.startswith(("https://", "http://", "ftp://", "magnet://")):
        url = "http://" + url

    message_link_match = message_link_regex.match(url)
    if message_link_match:
        group, msgid_str = message_link_match.groups()
        msgid = int(msgid_str)

        portal = po.Portal.find_by_username(group)
        if portal:
            message = DBMessage.get_by_tgid(TelegramID(msgid), portal.tgid)
            if message:
                url = f"https://matrix.to/#/{portal.mxid}/{message.mxid}"

    html.append(f"<a href='{url}'>{entity_text}</a>")
    return False


def init_tg(context: "Context") -> None:
    global should_highlight_edits
    should_highlight_edits = htmldiff and context.config["bridge.highlight_edits"]
