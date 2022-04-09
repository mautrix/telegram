# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2021 Tulir Asokan
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
from __future__ import annotations

from html import escape
import logging
import re

from telethon.errors import RPCError
from telethon.helpers import add_surrogate, del_surrogate, within_surrogate
from telethon.tl.custom import Message
from telethon.tl.types import (
    MessageEntityBlockquote,
    MessageEntityBold,
    MessageEntityBotCommand,
    MessageEntityCashtag,
    MessageEntityCode,
    MessageEntityEmail,
    MessageEntityHashtag,
    MessageEntityItalic,
    MessageEntityMention,
    MessageEntityMentionName,
    MessageEntityPhone,
    MessageEntityPre,
    MessageEntitySpoiler,
    MessageEntityStrike,
    MessageEntityTextUrl,
    MessageEntityUnderline,
    MessageEntityUrl,
    MessageFwdHeader,
    PeerChannel,
    PeerChat,
    PeerUser,
    SponsoredMessage,
    TypeMessageEntity,
)

from mautrix.appservice import IntentAPI
from mautrix.types import (
    EventType,
    Format,
    InReplyTo,
    MessageType,
    RelatesTo,
    TextMessageEventContent,
)

from .. import abstract_user as au, portal as po, puppet as pu, user as u
from ..db import Message as DBMessage
from ..types import TelegramID

log: logging.Logger = logging.getLogger("mau.fmt.tg")


async def telegram_reply_to_matrix(evt: Message, source: au.AbstractUser) -> RelatesTo | None:
    if evt.reply_to:
        space = (
            evt.peer_id.channel_id
            if isinstance(evt, Message) and isinstance(evt.peer_id, PeerChannel)
            else source.tgid
        )
        msg = await DBMessage.get_one_by_tgid(TelegramID(evt.reply_to.reply_to_msg_id), space)
        if msg:
            return RelatesTo(in_reply_to=InReplyTo(event_id=msg.mxid))
    return None


async def _add_forward_header(
    source: au.AbstractUser, content: TextMessageEventContent, fwd_from: MessageFwdHeader
) -> None:
    fwd_from_html, fwd_from_text = None, None
    if isinstance(fwd_from.from_id, PeerUser):
        user = await u.User.get_by_tgid(TelegramID(fwd_from.from_id.user_id))
        if user:
            fwd_from_text = user.displayname or user.mxid
            fwd_from_html = (
                f"<a href='https://matrix.to/#/{user.mxid}'>{escape(fwd_from_text)}</a>"
            )

        if not fwd_from_text:
            puppet = await pu.Puppet.get_by_peer(fwd_from.from_id, create=False)
            if puppet and puppet.displayname:
                fwd_from_text = puppet.displayname or puppet.mxid
                fwd_from_html = (
                    f"<a href='https://matrix.to/#/{puppet.mxid}'>{escape(fwd_from_text)}</a>"
                )

        if not fwd_from_text:
            try:
                user = await source.client.get_entity(fwd_from.from_id)
                if user:
                    fwd_from_text, _ = pu.Puppet.get_displayname(user, False)
                    fwd_from_html = f"<b>{escape(fwd_from_text)}</b>"
            except (ValueError, RPCError):
                fwd_from_text = fwd_from_html = "unknown user"
    elif isinstance(fwd_from.from_id, (PeerChannel, PeerChat)):
        from_id = (
            fwd_from.from_id.chat_id
            if isinstance(fwd_from.from_id, PeerChat)
            else fwd_from.from_id.channel_id
        )
        portal = await po.Portal.get_by_tgid(TelegramID(from_id))
        if portal and portal.title:
            fwd_from_text = portal.title
            if portal.alias:
                fwd_from_html = (
                    f"<a href='https://matrix.to/#/{portal.alias}'>{escape(fwd_from_text)}</a>"
                )
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

    content.ensure_has_html()
    content.body = "\n".join([f"> {line}" for line in content.body.split("\n")])
    content.body = f"Forwarded from {fwd_from_text}:\n{content.body}"
    content.formatted_body = (
        f"Forwarded message from {fwd_from_html}<br/>"
        f"<tg-forward><blockquote>{content.formatted_body}</blockquote></tg-forward>"
    )


async def _add_reply_header(
    source: au.AbstractUser, content: TextMessageEventContent, evt: Message, main_intent: IntentAPI
) -> None:
    space = (
        evt.peer_id.channel_id
        if isinstance(evt, Message) and isinstance(evt.peer_id, PeerChannel)
        else source.tgid
    )

    msg = await DBMessage.get_one_by_tgid(TelegramID(evt.reply_to.reply_to_msg_id), space)
    if not msg:
        return

    try:
        event = await main_intent.get_event(msg.mx_room, msg.mxid)
        if event.type == EventType.ROOM_ENCRYPTED and source.bridge.matrix.e2ee:
            event = await source.bridge.matrix.e2ee.decrypt(event)
        if isinstance(event.content, TextMessageEventContent):
            event.content.trim_reply_fallback()
        puppet = await pu.Puppet.get_by_mxid(event.sender, create=False)
        content.set_reply(event, displayname=puppet.displayname if puppet else event.sender)
    except Exception:
        log.exception("Failed to get event to add reply fallback")
        content.set_reply(msg.mxid)


async def telegram_to_matrix(
    evt: Message | SponsoredMessage,
    source: au.AbstractUser,
    main_intent: IntentAPI | None = None,
    prefix_text: str | None = None,
    prefix_html: str | None = None,
    override_text: str = None,
    override_entities: list[TypeMessageEntity] = None,
    no_reply_fallback: bool = False,
    require_html: bool = False,
) -> TextMessageEventContent:
    content = TextMessageEventContent(
        msgtype=MessageType.TEXT,
        body=add_surrogate(override_text or evt.message),
    )
    entities = override_entities or evt.entities
    if entities:
        content.format = Format.HTML
        html = await _telegram_entities_to_matrix_catch(add_surrogate(content.body), entities)
        content.formatted_body = del_surrogate(html)

    if require_html:
        content.ensure_has_html()

    if prefix_html:
        content.ensure_has_html()
        content.formatted_body = prefix_html + content.formatted_body
    if prefix_text:
        content.body = prefix_text + content.body

    if getattr(evt, "fwd_from", None):
        await _add_forward_header(source, content, evt.fwd_from)

    if getattr(evt, "reply_to", None) and not no_reply_fallback:
        await _add_reply_header(source, content, evt, main_intent)

    if isinstance(evt, Message) and evt.post and evt.post_author:
        content.ensure_has_html()
        content.body += f"\n- {evt.post_author}"
        content.formatted_body += f"<br/><i>- <u>{evt.post_author}</u></i>"

    return content


async def _telegram_entities_to_matrix_catch(text: str, entities: list[TypeMessageEntity]) -> str:
    try:
        return await _telegram_entities_to_matrix(text, entities)
    except Exception:
        log.exception(
            "Failed to convert Telegram format:\nmessage=%s\nentities=%s", text, entities
        )
    return "[failed conversion in _telegram_entities_to_matrix]"


async def _telegram_entities_to_matrix(
    text: str,
    entities: list[TypeMessageEntity],
    offset: int = 0,
    length: int = None,
    in_codeblock: bool = False,
) -> str:
    def text_to_html(
        val: str, _in_codeblock: bool = in_codeblock, escape_html: bool = True
    ) -> str:
        if escape_html:
            val = escape(val)
        if not _in_codeblock:
            val = val.replace("\n", "<br/>")
        return val

    if not entities:
        return text_to_html(text)
    if length is None:
        length = len(text)
    html = []
    last_offset = 0
    for i, entity in enumerate(entities):
        if entity.offset >= offset + length:
            break
        relative_offset = entity.offset - offset
        if relative_offset > last_offset:
            html.append(text_to_html(text[last_offset:relative_offset]))
        elif relative_offset < last_offset:
            continue

        while within_surrogate(text, relative_offset, length=length):
            relative_offset += 1
        while within_surrogate(text, relative_offset + entity.length, length=length):
            entity.length += 1

        skip_entity = False
        is_code_entity = isinstance(entity, (MessageEntityCode, MessageEntityPre))
        entity_text = await _telegram_entities_to_matrix(
            text=text[relative_offset : relative_offset + entity.length],
            entities=entities[i + 1 :],
            offset=entity.offset,
            length=entity.length,
            in_codeblock=is_code_entity,
        )
        entity_text = text_to_html(entity_text, is_code_entity, escape_html=False)
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
            html.append(
                f"<pre><code>{entity_text}</code></pre>"
                if "\n" in entity_text
                else f"<code>{entity_text}</code>"
            )
        elif entity_type == MessageEntityPre:
            skip_entity = _parse_pre(html, entity_text, entity.language)
        elif entity_type == MessageEntityMention:
            skip_entity = await _parse_mention(html, entity_text)
        elif entity_type == MessageEntityMentionName:
            skip_entity = await _parse_name_mention(html, entity_text, TelegramID(entity.user_id))
        elif entity_type == MessageEntityEmail:
            html.append(f"<a href='mailto:{entity_text}'>{entity_text}</a>")
        elif entity_type in (MessageEntityTextUrl, MessageEntityUrl):
            await _parse_url(
                html, entity_text, entity.url if entity_type == MessageEntityTextUrl else None
            )
        elif entity_type in (
            MessageEntityBotCommand,
            MessageEntityHashtag,
            MessageEntityCashtag,
            MessageEntityPhone,
        ):
            html.append(f"<font color='#3771bb'>{entity_text}</font>")
        elif entity_type == MessageEntitySpoiler:
            html.append(f"<span data-mx-spoiler>{entity_text}</span>")
        else:
            skip_entity = True
        last_offset = relative_offset + (0 if skip_entity else entity.length)
    html.append(text_to_html(text[last_offset:]))

    return "".join(html)


def _parse_pre(html: list[str], entity_text: str, language: str) -> bool:
    if language:
        html.append(f"<pre><code class='language-{language}'>{entity_text}</code></pre>")
    else:
        html.append(f"<pre><code>{entity_text}</code></pre>")
    return False


async def _parse_mention(html: list[str], entity_text: str) -> bool:
    username = entity_text[1:]

    mxid = None
    portal = None
    # This is a bit complicated because public channels have both Puppet and Portal instances.
    # Basically the currently intended output is:
    # User/bot mention (bridge user)          -> real user mention
    # User/bot mention (normal Telegram user) -> ghost user mention
    # Public channel with existing portal     -> room mention
    # Public channel without portal           -> ghost user mention
    # Other chat                              -> room mention
    user = await u.User.find_by_username(username) or await pu.Puppet.find_by_username(username)
    if user:
        if isinstance(user, pu.Puppet) and user.is_channel:
            portal = await po.Portal.get_by_tgid(user.tgid)
        mxid = user.mxid
    else:
        portal = await po.Portal.find_by_username(username)
    if portal and (portal.mxid or not user):
        mxid = portal.alias or portal.mxid

    if mxid:
        html.append(f"<a href='https://matrix.to/#/{mxid}'>{entity_text}</a>")
    else:
        return True
    return False


async def _parse_name_mention(html: list[str], entity_text: str, user_id: TelegramID) -> bool:
    user = await u.User.get_by_tgid(user_id)
    if user:
        mxid = user.mxid
    else:
        puppet = await pu.Puppet.get_by_tgid(user_id, create=False)
        mxid = puppet.mxid if puppet else None
    if mxid:
        html.append(f"<a href='https://matrix.to/#/{mxid}'>{entity_text}</a>")
    else:
        return True
    return False


message_link_regex = re.compile(
    r"https?://t(?:elegram)?\.(?:me|dog)"
    # /username or /c/id
    r"/([A-Za-z][A-Za-z0-9_]{3,31}[A-Za-z0-9]|[Cc]/[0-9]{1,20})"
    # /messageid
    r"/([0-9]{1,20})"
)


async def _parse_url(html: list[str], entity_text: str, url: str):
    url = escape(url) if url else entity_text
    if not url.startswith(("https://", "http://", "ftp://", "magnet://")):
        url = "http://" + url

    message_link_match = message_link_regex.match(url)
    if message_link_match:
        group, msgid_str = message_link_match.groups()
        msgid = int(msgid_str)

        if group.lower().startswith("c/"):
            portal = await po.Portal.get_by_tgid(TelegramID(int(group[2:])))
        else:
            portal = await po.Portal.find_by_username(group)
        if portal:
            message = await DBMessage.get_one_by_tgid(TelegramID(msgid), portal.tgid)
            if message:
                url = f"https://matrix.to/#/{portal.mxid}/{message.mxid}"

    html.append(f"<a href='{url}'>{entity_text}</a>")
