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
from typing import Dict, List, NewType, Optional, Tuple, Union

from mautrix_appservice import MatrixRequestError, IntentAPI

from ..types import MatrixRoomID, MatrixUserID
from . import command_handler, CommandEvent, SECTION_ADMIN
from .. import puppet as pu, portal as po

ManagementRoom = NewType('ManagementRoom', Tuple[MatrixRoomID, MatrixUserID])


async def _find_rooms(intent: IntentAPI) -> Tuple[List[ManagementRoom], List[MatrixRoomID],
                                                  List['po.Portal'], List['po.Portal']]:
    management_rooms = []  # type: List[ManagementRoom]
    unidentified_rooms = []  # type: List[MatrixRoomID]
    portals = []  # type: List[po.Portal]
    empty_portals = []  # type: List[po.Portal]

    rooms = await intent.get_joined_rooms()
    for room_str in rooms:
        room = MatrixRoomID(room_str)
        portal = po.Portal.get_by_mxid(room)
        if not portal:
            try:
                members = await intent.get_room_members(room)
            except MatrixRequestError:
                members = []
            if len(members) == 2:
                other_member = MatrixUserID(members[0] if members[0] != intent.mxid else members[1])
                if pu.Puppet.get_id_from_mxid(other_member):
                    unidentified_rooms.append(room)
                else:
                    management_rooms.append(ManagementRoom((room, other_member)))
            else:
                unidentified_rooms.append(room)
        else:
            members = await portal.get_authenticated_matrix_users()
            if len(members) == 0:
                empty_portals.append(portal)
            else:
                portals.append(portal)

    return management_rooms, unidentified_rooms, portals, empty_portals


@command_handler(needs_admin=True, needs_auth=False, management_only=True, name="clean-rooms",
                 help_section=SECTION_ADMIN,
                 help_text="Clean up unused portal/management rooms.")
async def clean_rooms(evt: CommandEvent) -> Optional[Dict]:
    management_rooms, unidentified_rooms, portals, empty_portals = await _find_rooms(evt.az.intent)

    reply = ["#### Management rooms (M)"]
    reply += ([f"{n+1}. [M{n+1}](https://matrix.to/#/{room}) (with {other_member}"
               for n, (room, other_member) in enumerate(management_rooms)]
              or ["No management rooms found."])
    reply.append("#### Active portal rooms (A)")
    reply += ([f"{n+1}. [A{n+1}](https://matrix.to/#/{portal.mxid}) "
               f"(to Telegram chat \"{portal.title}\")"
               for n, portal in enumerate(portals)]
              or ["No active portal rooms found."])
    reply.append("#### Unidentified rooms (U)")
    reply += ([f"{n+1}. [U{n+1}](https://matrix.to/#/{room})"
               for n, room in enumerate(unidentified_rooms)]
              or ["No unidentified rooms found."])
    reply.append("#### Inactive portal rooms (I)")
    reply += ([f"{n}. [I{n}](https://matrix.to/#/{portal.mxid}) "
               f"(to Telegram chat \"{portal.title}\")"
               for n, portal in enumerate(empty_portals)]
              or ["No inactive portal rooms found."])

    reply += ["#### Usage",
              ("To clean the recommended set of rooms (unidentified & inactive portals), "
               "type `$cmdprefix+sp clean-recommended`"),
              "",
              ("To clean other groups of rooms, type `$cmdprefix+sp clean-groups <letters>` "
               "where `letters` are the first letters of the group names (M, A, U, I)"),
              "",
              ("To clean specific rooms, type `$cmdprefix+sp clean-range <range>` "
               "where `range` is the range (e.g. `5-21`) prefixed with the first letter of"
               "the group name. (e.g. `I2-6`)"),
              "",
              ("Please note that you will have to re-run `$cmdprefix+sp clean-rooms` "
               "between each use of the commands above.")]

    evt.sender.command_status = {
        "next": lambda clean_evt: set_rooms_to_clean(clean_evt, management_rooms,
                                                     unidentified_rooms, portals, empty_portals),
        "action": "Room cleaning",
    }

    return await evt.reply("\n".join(reply))


async def set_rooms_to_clean(evt, management_rooms: List[ManagementRoom],
                             unidentified_rooms: List[MatrixRoomID], portals: List["po.Portal"],
                             empty_portals: List["po.Portal"]) -> None:
    command = evt.args[0]
    rooms_to_clean = []  # type: List[Union[po.Portal, MatrixRoomID]]
    if command == "clean-recommended":
        rooms_to_clean += empty_portals
        rooms_to_clean += unidentified_rooms
    elif command == "clean-groups":
        if len(evt.args) < 2:
            return await evt.reply("**Usage:** `$cmdprefix+sp clean-groups [M][A][U][I]")
        groups_to_clean = evt.args[1].upper()
        if "M" in groups_to_clean:
            rooms_to_clean += [room_id for (room_id, user_id) in management_rooms]
        if "A" in groups_to_clean:
            rooms_to_clean += portals
        if "U" in groups_to_clean:
            rooms_to_clean += unidentified_rooms
        if "I" in groups_to_clean:
            rooms_to_clean += empty_portals
    elif command == "clean-range":
        try:
            clean_range = evt.args[1]
            group, clean_range = clean_range[0], clean_range[1:]
            start, end = clean_range.split("-")
            start, end = int(start), int(end)
            if group == "M":
                group = [room_id for (room_id, user_id) in management_rooms]
            elif group == "A":
                group = portals
            elif group == "U":
                group = unidentified_rooms
            elif group == "I":
                group = empty_portals
            else:
                raise ValueError("Unknown group")
            rooms_to_clean = group[start - 1:end]
        except (KeyError, ValueError):
            return await evt.reply(
                "**Usage:** `$cmdprefix+sp clean-groups <_M|A|U|I_><range>")
    else:
        return await evt.reply(f"Unknown room cleaning action `{command}`. "
                               "Use `$cmdprefix+sp cancel` to cancel room "
                               "cleaning.")

    evt.sender.command_status = {
        "next": lambda confirm: execute_room_cleanup(confirm, rooms_to_clean),
        "action": "Room cleaning",
    }
    await evt.reply(f"To confirm cleaning up {len(rooms_to_clean)} rooms, type"
                    "`$cmdprefix+sp confirm-clean`.")


async def execute_room_cleanup(evt, rooms_to_clean: List[Union[po.Portal, MatrixRoomID]]) -> None:
    if len(evt.args) > 0 and evt.args[0] == "confirm-clean":
        await evt.reply(f"Cleaning {len(rooms_to_clean)} rooms. "
                        "This might take a while.")
        cleaned = 0
        for room in rooms_to_clean:
            if isinstance(room, po.Portal):
                await room.cleanup_and_delete()
                cleaned += 1
            elif isinstance(room, str):  # str is aliased by MatrixRoomID
                await po.Portal.cleanup_room(evt.az.intent, room, message="Room deleted")
                cleaned += 1
        evt.sender.command_status = None
        await evt.reply(f"{cleaned} rooms cleaned up successfully.")
    else:
        await evt.reply("Room cleaning cancelled.")
