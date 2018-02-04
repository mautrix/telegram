# -*- coding: future_fstrings -*-
# matrix-appservice-python - A Matrix Application Service framework written in Python.
# Copyright (C) 2018 Tulir Asokan
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
import json


class StateStore:
    def __init__(self, autosave_file=None):
        self.registrations = set()
        self.memberships = {}
        self.power_levels = {}
        self.autosave_file = autosave_file

    def save(self, file):
        if isinstance(file, str):
            output = open(file, "w")
        else:
            output = file

        json.dump({
            "registrations": list(self.registrations),
            "memberships": self.memberships,
            "power_levels": self.power_levels,
        }, output)

        if isinstance(file, str):
            output.close()

    def load(self, file):
        if isinstance(file, str):
            try:
                input = open(file, "r")
            except FileNotFoundError:
                return
        else:
            input = file

        data = json.load(input)
        if "registrations" in data:
            self.registrations = set(data["registrations"])
        if "memberships" in data:
            self.memberships = data["memberships"]
        if "power_levels" in data:
            self.power_levels = data["power_levels"]

        if isinstance(file, str):
            input.close()

    def _autosave(self):
        if self.autosave_file:
            self.save(self.autosave_file)

    def is_registered(self, user):
        return user in self.registrations

    def registered(self, user):
        self.registrations.add(user)
        self._autosave()

    def _get_membership(self, room, user):
        return self.memberships.get(room, {}).get(user, "left")

    def is_joined(self, room, user):
        return self._get_membership(room, user) == "join"

    def _set_membership(self, room, user, membership):
        if room not in self.memberships:
            self.memberships[room] = {}
        self.memberships[room][user] = membership
        self._autosave()

    def joined(self, room, user):
        return self._set_membership(room, user, "join")

    def invited(self, room, user):
        return self._set_membership(room, user, "invite")

    def left(self, room, user):
        return self._set_membership(room, user, "left")

    def has_power_level_data(self, room):
        return room in self.power_levels

    def has_power_level(self, room, user, event):
        room_levels = self.power_levels.get(room, {})
        required = room_levels["events"].get(event, 95)
        has = room_levels["users"].get(user, 0)
        return has >= required

    def set_power_level(self, room, user, level):
        if not room in self.power_levels:
            self.power_levels[room] = {
                "users": {},
                "events": {},
            }
        self.power_levels[room]["users"][user] = level
        self._autosave()

    def set_power_levels(self, room, content):
        if "events" not in content:
            content["events"] = {}
        if "users" not in content:
            content["users"] = {}
        self.power_levels[room] = content
        self._autosave()
