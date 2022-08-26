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

from typing import Iterable

from telethon.errors import ChatAdminRequiredError
from telethon.tl.functions.channels import GetParticipantsRequest
from telethon.tl.functions.messages import GetFullChatRequest
from telethon.tl.types import (
    ChannelParticipantBanned,
    ChannelParticipantsRecent,
    ChannelParticipantsSearch,
    ChatParticipantsForbidden,
    InputChannel,
    InputUser,
    TypeChannelParticipant,
    TypeChat,
    TypeChatParticipant,
    TypeInputPeer,
    TypeUser,
)

from ..tgclient import MautrixTelegramClient


def _filter_participants(
    users: list[TypeUser], participants: list[TypeChatParticipant | TypeChannelParticipant]
) -> Iterable[TypeUser]:
    participant_map = {
        part.user_id: part
        for part in participants
        if not isinstance(part, ChannelParticipantBanned)
    }
    for user in users:
        try:
            user.participant = participant_map[user.id]
        except KeyError:
            pass
        else:
            yield user


async def _get_channel_users(
    client: MautrixTelegramClient, entity: InputChannel, limit: int
) -> list[TypeUser]:
    if 0 < limit <= 200:
        response = await client(
            GetParticipantsRequest(
                entity, ChannelParticipantsRecent(), offset=0, limit=limit, hash=0
            )
        )
        return list(_filter_participants(response.users, response.participants))
    elif limit > 200 or limit == -1:
        users: list[TypeUser] = []
        offset = 0
        remaining_quota = limit if limit > 0 else 1000000
        query = ChannelParticipantsSearch("") if limit == -1 else ChannelParticipantsRecent()
        while True:
            if remaining_quota <= 0:
                break
            response = await client(
                GetParticipantsRequest(
                    entity, query, offset=offset, limit=min(remaining_quota, 200), hash=0
                )
            )
            if not response.users:
                break
            users += _filter_participants(response.users, response.participants)
            offset += len(response.participants)
            remaining_quota -= len(response.participants)
        return users


async def get_users(
    client: MautrixTelegramClient,
    tgid: int,
    entity: TypeInputPeer | InputUser | TypeChat | TypeUser | InputChannel,
    limit: int,
    peer_type: str,
) -> list[TypeUser]:
    if peer_type == "chat":
        chat = await client(GetFullChatRequest(chat_id=tgid))
        if isinstance(chat.full_chat.participants, ChatParticipantsForbidden):
            return []
        users = list(_filter_participants(chat.users, chat.full_chat.participants.participants))
        return users[:limit] if limit > 0 else users
    elif peer_type == "channel":
        try:
            return await _get_channel_users(client, entity, limit)
        except ChatAdminRequiredError:
            return []
    elif peer_type == "user":
        return [entity]
    else:
        raise RuntimeError(f"Unexpected peer type {peer_type}")
