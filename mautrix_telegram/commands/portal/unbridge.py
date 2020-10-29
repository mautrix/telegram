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
from typing import Dict, Callable, Optional

from mautrix.types import RoomID, EventID

from ... import portal as po
from .. import command_handler, CommandEvent, SECTION_PORTAL_MANAGEMENT
from .util import user_has_power_level


async def _get_portal_and_check_permission(evt: CommandEvent) -> Optional[po.Portal]:
    room_id = RoomID(evt.args[0]) if len(evt.args) > 0 else evt.room_id

    portal = po.Portal.get_by_mxid(room_id)
    if not portal:
        that_this = "This" if room_id == evt.room_id else "That"
        await evt.reply(f"{that_this} is not a portal room.")
        return None

    if portal.peer_type == "user":
        if portal.tg_receiver != evt.sender.tgid:
            await evt.reply("You do not have the permissions to unbridge that portal.")
            return None
        return portal

    if not await user_has_power_level(portal.mxid, evt.az.intent, evt.sender, "unbridge"):
        await evt.reply("You do not have the permissions to unbridge that portal.")
        return None
    return portal


def _get_portal_murder_function(action: str, room_id: str, function: Callable, command: str,
                                completed_message: str) -> Dict:
    async def post_confirm(confirm) -> Optional[EventID]:
        confirm.sender.command_status = None
        if len(confirm.args) > 0 and confirm.args[0] == f"confirm-{command}":
            await function()
            if confirm.room_id != room_id:
                return await confirm.reply(completed_message)
        else:
            return await confirm.reply(f"{action} cancelled.")
        return None

    return {
        "next": post_confirm,
        "action": action,
    }


@command_handler(needs_auth=False, needs_puppeting=False,
                 help_section=SECTION_PORTAL_MANAGEMENT,
                 help_text="Remove all users from the current portal room and forget the portal. "
                           "Only works for group chats; to delete a private chat portal, simply "
                           "leave the room.")
async def delete_portal(evt: CommandEvent) -> Optional[EventID]:
    portal = await _get_portal_and_check_permission(evt)
    if not portal:
        return None

    evt.sender.command_status = _get_portal_murder_function("Portal deletion", portal.mxid,
                                                            portal.cleanup_and_delete, "delete",
                                                            "Portal successfully deleted.")
    return await evt.reply("Please confirm deletion of portal "
                           f"[{portal.alias or portal.mxid}](https://matrix.to/#/{portal.mxid}) "
                           f"to Telegram chat \"{portal.title}\" "
                           "by typing `$cmdprefix+sp confirm-delete`"
                           "\n\n"
                           "**WARNING:** If the bridge bot has the power level to do so, **this "
                           "will kick ALL users** in the room. If you just want to remove the "
                           "bridge, use `$cmdprefix+sp unbridge` instead.")


@command_handler(needs_auth=False, needs_puppeting=False,
                 help_section=SECTION_PORTAL_MANAGEMENT,
                 help_text="Remove puppets from the current portal room and forget the portal.")
async def unbridge(evt: CommandEvent) -> Optional[EventID]:
    portal = await _get_portal_and_check_permission(evt)
    if not portal:
        return None

    evt.sender.command_status = _get_portal_murder_function("Room unbridging", portal.mxid,
                                                            portal.unbridge, "unbridge",
                                                            "Room successfully unbridged.")
    return await evt.reply(f"Please confirm unbridging chat \"{portal.title}\" from room "
                           f"[{portal.alias or portal.mxid}](https://matrix.to/#/{portal.mxid}) "
                           "by typing `$cmdprefix+sp confirm-unbridge`")
