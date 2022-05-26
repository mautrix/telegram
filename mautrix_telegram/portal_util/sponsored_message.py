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

import base64
import html

from telethon.tl.functions.channels import GetSponsoredMessagesRequest
from telethon.tl.types import Channel, InputChannel, PeerChannel, PeerUser, SponsoredMessage, User

from mautrix.types import MessageType, TextMessageEventContent

from .. import user as u
from ..formatter import telegram_to_matrix


async def get_sponsored_message(
    user: u.User,
    entity: InputChannel,
) -> tuple[SponsoredMessage | None, int | None, Channel | User | None]:
    resp = await user.client(GetSponsoredMessagesRequest(entity))
    if len(resp.messages) == 0:
        return None, None, None
    msg = resp.messages[0]
    if isinstance(msg.from_id, PeerUser):
        entities = resp.users
        target_id = msg.from_id.user_id
    else:
        entities = resp.chats
        target_id = msg.from_id.channel_id
    try:
        entity = next(ent for ent in entities if ent.id == target_id)
    except StopIteration:
        entity = None
    return msg, target_id, entity


async def make_sponsored_message_content(
    source: u.User, msg: SponsoredMessage, entity: Channel | User
) -> TextMessageEventContent | None:
    content = await telegram_to_matrix(msg, source, require_html=True)
    content.external_url = f"https://t.me/{entity.username}"
    content.msgtype = MessageType.NOTICE
    sponsored_meta = {
        "random_id": base64.b64encode(msg.random_id).decode("utf-8"),
    }
    if isinstance(msg.from_id, PeerChannel):
        sponsored_meta["channel_id"] = msg.from_id.channel_id
        if getattr(msg, "channel_post", None) is not None:
            sponsored_meta["channel_post"] = msg.channel_post
            content.external_url += f"/{msg.channel_post}"
            action = "View Post"
        else:
            action = "View Channel"
    elif isinstance(msg.from_id, PeerUser):
        sponsored_meta["bot_id"] = msg.from_id.user_id
        if msg.start_param:
            content.external_url += f"?start={msg.start_param}"
        action = "View Bot"
    else:
        return None

    if isinstance(entity, User):
        name_parts = [entity.first_name, entity.last_name]
        sponsor_name = " ".join(x for x in name_parts if x)
        sponsor_name_html = f"<strong>{html.escape(sponsor_name)}</strong>"
    elif isinstance(entity, Channel):
        sponsor_name = entity.title
        sponsor_name_html = f"<strong>{html.escape(sponsor_name)}</strong>"
    else:
        sponsor_name = sponsor_name_html = "unknown entity"

    content["fi.mau.telegram.sponsored"] = sponsored_meta
    content.formatted_body += (
        f"<br/><br/>Sponsored message from {sponsor_name_html} "
        f"- <a href='{content.external_url}'>{action}</a>"
    )
    content.body += (
        f"\n\nSponsored message from {sponsor_name} - {action} at {content.external_url}"
    )

    return content
