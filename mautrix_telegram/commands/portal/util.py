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

from mautrix.appservice import IntentAPI
from mautrix.errors import MatrixRequestError
from mautrix.types import EventType, PowerLevelStateEventContent, RoomID

from ... import user as u
from .. import CommandEvent


async def get_initial_state(
    intent: IntentAPI, room_id: RoomID
) -> tuple[str | None, str | None, PowerLevelStateEventContent | None, bool]:
    state = await intent.get_state(room_id)
    title: str | None = None
    about: str | None = None
    levels: PowerLevelStateEventContent | None = None
    encrypted: bool = False
    for event in state:
        try:
            if event.type == EventType.ROOM_NAME:
                title = event.content.name
            elif event.type == EventType.ROOM_TOPIC:
                about = event.content.topic
            elif event.type == EventType.ROOM_POWER_LEVELS:
                levels = event.content
            elif event.type == EventType.ROOM_CANONICAL_ALIAS:
                title = title or event.content.canonical_alias
            elif event.type == EventType.ROOM_ENCRYPTION:
                encrypted = True
        except KeyError:
            # Some state event probably has empty content
            pass
    return title, about, levels, encrypted


async def warn_missing_power(levels: PowerLevelStateEventContent, evt: CommandEvent) -> None:
    if levels.get_user_level(evt.az.bot_mxid) < levels.redact:
        await evt.reply(
            "Warning: The bot does not have privileges to redact messages on Matrix. "
            "Message deletions from Telegram will not be bridged unless you give "
            f"redaction permissions to [{evt.az.bot_mxid}](https://matrix.to/#/{evt.az.bot_mxid})"
        )


async def user_has_power_level(
    room_id: RoomID, intent: IntentAPI, sender: u.User, event: str
) -> bool:
    if sender.is_admin:
        return True
    # Make sure the state store contains the power levels.
    try:
        await intent.get_power_levels(room_id)
    except MatrixRequestError:
        return False
    event_type = EventType.find(f"fi.mau.telegram.{event}", t_class=EventType.Class.STATE)
    return await intent.state_store.has_power_level(room_id, sender.mxid, event_type)
