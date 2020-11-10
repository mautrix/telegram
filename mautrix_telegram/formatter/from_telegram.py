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
from typing import List, Optional, TYPE_CHECKING
from html import escape
import logging
import re

from telethon.tl.types import (MessageEntityMention, MessageEntityMentionName, MessageEntityUrl,
                               MessageEntityEmail, MessageEntityTextUrl, MessageEntityBold,
                               MessageEntityItalic, MessageEntityCode, MessageEntityPre,
                               MessageEntityBotCommand, MessageEntityHashtag, MessageEntityCashtag,
                               MessageEntityPhone, TypeMessageEntity, PeerChannel, PeerChat,
                               MessageEntityBlockquote, MessageEntityStrike, MessageFwdHeader,
                               MessageEntityUnderline, PeerUser)
from telethon.tl.custom import Message
from telethon.errors import RPCError
from telethon.helpers import add_surrogate, del_surrogate

from mautrix.errors import MatrixRequestError
from mautrix.appservice import IntentAPI
from mautrix.types import (TextMessageEventContent, RelatesTo, RelationType, Format, MessageType,
                           MessageEvent)

from .. import user as u, puppet as pu, portal as po
from ..types import TelegramID
from ..db import Message as DBMessage

if TYPE_CHECKING:
    from ..abstract_user import AbstractUser

log: logging.Logger = logging.getLogger("mau.fmt.tg")


def telegram_reply_to_matrix(evt: Message, source: 'AbstractUser') -> Optional[RelatesTo]:
    if evt.reply_to:
        space = (evt.peer_id.channel_id
                 if isinstance(evt, Message) and isinstance(evt.peer_id, PeerChannel)
                 else source.tgid)
        msg = DBMessage.get_one_by_tgid(TelegramID(evt.reply_to.reply_to_msg_id), space)
        if msg:
            return RelatesTo(rel_type=RelationType.REPLY, event_id=msg.mxid)
    return None


async def _add_forward_header(source: 'AbstractUser', content: TextMessageEventContent,
                              fwd_from: MessageFwdHeader) -> None:
    if not content.formatted_body or content.format != Format.HTML:
        content.format = Format.HTML
        content.formatted_body = escape(content.body)
    fwd_from_html, fwd_from_text = None, None
    if isinstance(fwd_from.from_id, PeerUser):
        user = u.User.get_by_tgid(TelegramID(fwd_from.from_id.user_id))
        if user:
            fwd_from_text = user.displayname or user.mxid
            fwd_from_html = (f"<a href='https://matrix.to/#/{user.mxid}'>"
                             f"{escape(fwd_from_text)}</a>")

        if not fwd_from_text:
            puppet = pu.Puppet.get(TelegramID(fwd_from.from_id.user_id), create=False)
            if puppet and puppet.displayname:
                fwd_from_text = puppet.displayname or puppet.mxid
                fwd_from_html = (f"<a href='https://matrix.to/#/{puppet.mxid}'>"
                                 f"{escape(fwd_from_text)}</a>")

        if not fwd_from_text:
            try:
                user = await source.client.get_entity(fwd_from.from_id)
                if user:
                    fwd_from_text = pu.Puppet.get_displayname(user, False)
                    fwd_from_html = f"<b>{escape(fwd_from_text)}</b>"
            except (ValueError, RPCError):
                fwd_from_text = fwd_from_html = "unknown user"
    elif isinstance(fwd_from.from_id, (PeerChannel, PeerChat)):
        from_id = (fwd_from.from_id.chat_id if isinstance(fwd_from.from_id, PeerChat)
                   else fwd_from.from_id.channel_id)
        portal = po.Portal.get_by_tgid(TelegramID(from_id))
        if portal:
            fwd_from_text = portal.title
            if portal.alias:
                fwd_from_html = (f"<a href='https://matrix.to/#/{portal.alias}'>"
                                 f"{escape(fwd_from_text)}</a>")
            else:
                fwd_from_html = f"channel <b>{escape(fwd_from_text)}</b>"
        else:
            try:
                channel = await source.client.get_entity(fwd_from.from_id)
                if channel:
                    fwd_from_text = f"channel {channel.title}"
                    fwd_from_html = f"channel <b>{escape(channel.title)}</b>"
            except (ValueError, RPCError):
                fwd_from_text = fwd_from_html = "unknown channel"
    elif fwd_from.from_name:
        fwd_from_text = fwd_from.from_name
        fwd_from_html = f"<b>{escape(fwd_from.from_name)}</b>"
    else:
        fwd_from_text = "unknown source"
        fwd_from_html = f"unknown source"

    content.body = "\n".join([f"> {line}" for line in content.body.split("\n")])
    content.body = f"Forwarded from {fwd_from_text}:\n{content.body}"
    content.formatted_body = (
        f"Forwarded message from {fwd_from_html}<br/>"
        f"<tg-forward><blockquote>{content.formatted_body}</blockquote></tg-forward>")


async def _add_reply_header(source: 'AbstractUser', content: TextMessageEventContent, evt: Message,
                            main_intent: IntentAPI):
    space = (evt.peer_id.channel_id
             if isinstance(evt, Message) and isinstance(evt.peer_id, PeerChannel)
             else source.tgid)

    msg = DBMessage.get_one_by_tgid(TelegramID(evt.reply_to.reply_to_msg_id), space)
    if not msg:
        return

    content.relates_to = RelatesTo(rel_type=RelationType.REPLY, event_id=msg.mxid)

    try:
        event: MessageEvent = await main_intent.get_event(msg.mx_room, msg.mxid)
        if isinstance(event.content, TextMessageEventContent):
            event.content.trim_reply_fallback()
        puppet = await pu.Puppet.get_by_mxid(event.sender, create=False)
        content.set_reply(event, displayname=puppet.displayname if puppet else event.sender)
    except MatrixRequestError:
        log.exception("Failed to get event to add reply fallback")


async def telegram_to_matrix(evt: Message, source: "AbstractUser",
                             main_intent: Optional[IntentAPI] = None,
                             prefix_text: Optional[str] = None, prefix_html: Optional[str] = None,
                             override_text: str = None,
                             override_entities: List[TypeMessageEntity] = None,
                             no_reply_fallback: bool = False) -> TextMessageEventContent:
    content = TextMessageEventContent(
        msgtype=MessageType.TEXT,
        body=add_surrogate(override_text or evt.message),
    )
    entities = override_entities or evt.entities
    if entities:
        content.format = Format.HTML
        content.formatted_body = _telegram_entities_to_matrix_catch(content.body, entities)

    if prefix_html:
        if not content.formatted_body:
            content.format = Format.HTML
            content.formatted_body = escape(content.body)
        content.formatted_body = prefix_html + content.formatted_body
    if prefix_text:
        content.body = prefix_text + content.body

    if evt.fwd_from:
        await _add_forward_header(source, content, evt.fwd_from)

    if evt.reply_to and not no_reply_fallback:
        await _add_reply_header(source, content, evt, main_intent)

    if isinstance(evt, Message) and evt.post and evt.post_author:
        if not content.formatted_body:
            content.formatted_body = escape(content.body)
        content.body += f"\n- {evt.post_author}"
        content.formatted_body += f"<br/><i>- <u>{evt.post_author}</u></i>"

    content.body = del_surrogate(content.body)

    if content.formatted_body:
        content.formatted_body = del_surrogate(content.formatted_body.replace("\n", "<br/>"))

    return content


def _telegram_entities_to_matrix_catch(text: str, entities: List[TypeMessageEntity]) -> str:
    try:
        return _telegram_entities_to_matrix(text, entities)
    except Exception:
        log.exception("Failed to convert Telegram format:\n"
                      "message=%s\n"
                      "entities=%s",
                      text, entities)
    return "[failed conversion in _telegram_entities_to_matrix]"


def _telegram_entities_to_matrix(text: str, entities: List[TypeMessageEntity],
                                 offset: int = 0, length: int = None) -> str:
    if not entities:
        return escape(text)
    if length is None:
        length = len(text)
    html = []
    last_offset = 0
    for i, entity in enumerate(entities):
        if entity.offset > offset + length:
            break
        relative_offset = entity.offset - offset
        if relative_offset > last_offset:
            html.append(escape(text[last_offset:relative_offset]))
        elif relative_offset < last_offset:
            continue

        skip_entity = False
        entity_text = _telegram_entities_to_matrix(
            text=text[relative_offset:relative_offset + entity.length],
            entities=entities[i + 1:], offset=entity.offset, length=entity.length)
        entity_type = type(entity)

        if entity_type == MessageEntityBold:
            html.append(f"<strong>{entity_text}</strong>")
        elif entity_type == MessageEntityItalic:
            html.append(f"<em>{entity_text}</em>")
        elif entity_type == MessageEntityUnderline:
            html.append(f"<u>{entity_text}</u>")
        elif entity_type == MessageEntityStrike:
            html.append(f"<del>{entity_text}</del>")
        elif entity_type == MessageEntityBlockquote:
            html.append(f"<blockquote>{entity_text}</blockquote>")
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
        last_offset = relative_offset + (0 if skip_entity else entity.length)
    html.append(escape(text[last_offset:]))

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


message_link_regex = re.compile(r"https?://t(?:elegram)?\.(?:me|dog)/"
                                r"([A-Za-z][A-Za-z0-9_]{3,}[A-Za-z0-9])/([0-9]{1,50})")


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
            message = DBMessage.get_one_by_tgid(TelegramID(msgid), portal.tgid)
            if message:
                url = f"https://matrix.to/#/{portal.mxid}/{message.mxid}"

    html.append(f"<a href='{url}'>{entity_text}</a>")
    return False
