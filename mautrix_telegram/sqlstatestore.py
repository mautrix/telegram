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
import json

from mautrix_appservice import StateStore

from . import puppet as pu
from .db import RoomState, UserProfile


class SQLStateStore(StateStore):
    def __init__(self, db):
        super().__init__()
        self.db = db

    def is_registered(self, user: str) -> bool:
        puppet = pu.Puppet.get_by_mxid(user)
        return puppet.is_registered if puppet else False

    def registered(self, user: str):
        puppet = pu.Puppet.get_by_mxid(user)
        if puppet:
            puppet.is_registered = True
            puppet.save()

    def update_state(self, event: dict):
        event_type = event["type"]
        if event_type == "m.room.power_levels":
            self.set_power_levels(event["room_id"], event["content"])
        elif event_type == "m.room.member":
            self.set_member(event["room_id"], event["state_key"], event["content"])

    def get_member(self, room: str, user: str) -> dict:
        profile = UserProfile.query.get((room, user))
        if profile:
            return profile.dict()
        return {}

    def set_member(self, room: str, user: str, member: dict):
        profile = UserProfile(room_id=room, user_id=user,
                              membership=member.get("membership", "leave"),
                              displayname=member.get("displayname", None),
                              avatar_url=member.get("avatar_url", None))
        self.db.merge(profile)
        self.db.commit()

    def set_membership(self, room: str, user: str, membership: str):
        profile = UserProfile.query.get((room, user))
        if not profile:
            profile = UserProfile(room_id=room, user_id=user, membership=membership)
            self.db.add(profile)
        else:
            profile.membership = membership
        self.db.commit()

    def has_power_levels(self, room: str) -> bool:
        room = RoomState.query.get(room)
        return room and room._power_levels_text

    def get_power_levels(self, room: str) -> dict:
        return RoomState.query.get(room).power_levels

    def set_power_level(self, room: str, user: str, level: int):
        room_state = RoomState.query.get(room)
        if not room_state:
            room_state = RoomState(room)
            self.db.add(room_state)

        power_levels = room_state.power_levels
        if not power_levels:
            power_levels = {
                "users": {},
                "events": {},
            }
        power_levels[room]["users"][user] = level
        room_state.power_levels = power_levels
        self.db.commit()

    def set_power_levels(self, room: str, content: dict):
        state = RoomState.query.get(room)
        if not state:
            state = RoomState(room_id=room)
            self.db.add(state)
        state.power_levels = content
        self.db.commit()
