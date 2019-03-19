# -*- coding: future_fstrings -*-
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
from typing import Dict, Tuple

from mautrix_appservice import MatrixRequestError, IntentAPI

from ... import user as u


async def get_initial_state(intent: IntentAPI, room_id: str) -> Tuple[str, str, Dict]:
    state = await intent.get_room_state(room_id)
    title = None
    about = None
    levels = None
    for event in state:
        try:
            if event["type"] == "m.room.name":
                title = event["content"]["name"]
            elif event["type"] == "m.room.topic":
                about = event["content"]["topic"]
            elif event["type"] == "m.room.power_levels":
                levels = event["content"]
            elif event["type"] == "m.room.canonical_alias":
                title = title or event["content"]["alias"]
        except KeyError:
            # Some state event probably has empty content
            pass
    return title, about, levels


async def user_has_power_level(room: str, intent, sender: u.User, event: str, default: int = 50
                               ) -> bool:
    if sender.is_admin:
        return True
    # Make sure the state store contains the power levels.
    try:
        await intent.get_power_levels(room)
    except MatrixRequestError:
        return False
    return intent.state_store.has_power_level(room, sender.mxid,
                                              event=f"net.maunium.telegram.{event}",
                                              default=default)
