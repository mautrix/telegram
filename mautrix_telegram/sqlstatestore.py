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

from mautrix_appservice import StateStore

from .types import MatrixUserID, MatrixRoomID
from . import puppet as pu
from .db import RoomState, UserProfile


class SQLStateStore(StateStore):
    def __init__(self) -> None:
        super().__init__()
        self.profile_cache = {}  # type: Dict[Tuple[str, str], UserProfile]
        self.room_state_cache = {}  # type: Dict[str, RoomState]

    @staticmethod
    def is_registered(user: MatrixUserID) -> bool:
        puppet = pu.Puppet.get_by_mxid(user)
        return puppet.is_registered if puppet else False

    @staticmethod
    def registered(user: MatrixUserID) -> None:
        puppet = pu.Puppet.get_by_mxid(user)
        if puppet:
            puppet.is_registered = True
            puppet.save()

    def update_state(self, event: Dict) -> None:
        event_type = event["type"]
        if event_type == "m.room.power_levels":
            self.set_power_levels(event["room_id"], event["content"])
        elif event_type == "m.room.member":
            self.set_member(event["room_id"], event["state_key"], event["content"])

    def _get_user_profile(self, room_id: MatrixRoomID, user_id: MatrixUserID, create: bool = True
                          ) -> UserProfile:
        key = (room_id, user_id)
        try:
            return self.profile_cache[key]
        except KeyError:
            pass

        profile = UserProfile.get(*key)
        if profile:
            self.profile_cache[key] = profile
        elif create:
            profile = UserProfile(room_id=room_id, user_id=user_id, membership="leave")
            profile.insert()
            self.profile_cache[key] = profile
        return profile

    def get_member(self, room: MatrixRoomID, user: MatrixUserID) -> Dict:
        return self._get_user_profile(room, user).dict()

    def set_member(self, room: MatrixRoomID, user: MatrixUserID, member: Dict) -> None:
        profile = self._get_user_profile(room, user)
        profile.membership = member.get("membership", profile.membership or "leave")
        profile.displayname = member.get("displayname", profile.displayname)
        profile.avatar_url = member.get("avatar_url", profile.avatar_url)
        profile.update()

    def set_membership(self, room: MatrixRoomID, user: MatrixUserID, membership: str) -> None:
        self.set_member(room, user, {
            "membership": membership,
        })

    def _get_room_state(self, room_id: MatrixRoomID, create: bool = True) -> RoomState:
        try:
            return self.room_state_cache[room_id]
        except KeyError:
            pass

        room = RoomState.get(room_id)
        if room:
            self.room_state_cache[room_id] = room
        elif create:
            room = RoomState(room_id=room_id)
            room.insert()
            self.room_state_cache[room_id] = room
        return room

    def has_power_levels(self, room: MatrixRoomID) -> bool:
        return bool(self._get_room_state(room).power_levels)

    def get_power_levels(self, room: MatrixRoomID) -> Dict:
        return self._get_room_state(room).power_levels

    def set_power_level(self, room: MatrixRoomID, user: MatrixUserID, level: int) -> None:
        room_state = self._get_room_state(room)
        power_levels = room_state.power_levels
        if not power_levels:
            power_levels = {
                "users": {},
                "events": {},
            }
        power_levels[room]["users"][user] = level
        room_state.power_levels = power_levels
        room_state.update()

    def set_power_levels(self, room: MatrixRoomID, content: Dict) -> None:
        state = self._get_room_state(room)
        state.power_levels = content
        state.update()
