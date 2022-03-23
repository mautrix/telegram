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

from typing import Awaitable
import asyncio

from telethon.tl.types import ChannelForbidden, ChatForbidden

from mautrix.types import EventID, RoomID

from ... import portal as po
from ...types import TelegramID
from .. import SECTION_CREATING_PORTALS, CommandEvent, command_handler
from .util import get_initial_state, user_has_power_level, warn_missing_power


@command_handler(
    needs_auth=False,
    needs_puppeting=False,
    help_section=SECTION_CREATING_PORTALS,
    help_args="[_id_]",
    help_text=(
        "Bridge the current Matrix room to the Telegram chat with the given ID. The ID must be "
        "the prefixed version that you get with the `/id` command of the Telegram-side bot."
    ),
)
async def bridge(evt: CommandEvent) -> EventID:
    if len(evt.args) == 0:
        return await evt.reply(
            "**Usage:** `$cmdprefix+sp bridge <Telegram chat ID> [Matrix room ID]`"
        )
    force_use_bot = False
    if evt.args[0] == "--usebot" and evt.sender.is_admin:
        force_use_bot = True
        evt.args = evt.args[1:]
    room_id = RoomID(evt.args[1]) if len(evt.args) > 1 else evt.room_id
    that_this = "This" if room_id == evt.room_id else "That"

    portal = await po.Portal.get_by_mxid(room_id)
    if portal:
        return await evt.reply(f"{that_this} room is already a portal room.")

    if not await user_has_power_level(room_id, evt.az.intent, evt.sender, "bridge"):
        return await evt.reply(f"You do not have the permissions to bridge {that_this} room.")

    # The /id bot command provides the prefixed ID, so we assume
    tgid_str = evt.args[0]
    tgid = None
    try:
        if tgid_str.startswith("-100"):
            tgid = TelegramID(int(tgid_str[4:]))
            peer_type = "channel"
        elif tgid_str.startswith("-"):
            tgid = TelegramID(-int(tgid_str))
            peer_type = "chat"
    except ValueError:
        # Invalid integer
        pass
    if not tgid:
        return await evt.reply(
            "That doesn't seem like a prefixed Telegram chat ID.\n\n"
            "If you did not get the ID using the `/id` bot command, please prefix"
            "channel/supergroup IDs with `-100` and non-super group IDs with `-`.\n\n"
            "Bridging private chats to existing rooms is not allowed."
        )

    portal = await po.Portal.get_by_tgid(tgid, peer_type=peer_type)
    if not portal.allow_bridging:
        return await evt.reply(
            "This bridge doesn't allow bridging that Telegram chat.\n"
            "If you're the bridge admin, try "
            "`$cmdprefix+sp filter whitelist <Telegram chat ID>` first."
        )
    elif portal.mxid:
        has_portal_message = (
            "That Telegram chat already has a portal at "
            f"[{portal.alias or portal.mxid}](https://matrix.to/#/{portal.mxid}). "
        )
        if not await user_has_power_level(portal.mxid, evt.az.intent, evt.sender, "unbridge"):
            return await evt.reply(
                f"{has_portal_message}"
                "Additionally, you do not have the permissions to unbridge that room."
            )
        evt.sender.command_status = {
            "next": confirm_bridge,
            "action": "Room bridging",
            "mxid": portal.mxid,
            "bridge_to_mxid": room_id,
            "tgid": portal.tgid,
            "peer_type": peer_type,
            "force_use_bot": force_use_bot,
        }
        return await evt.reply(
            f"{has_portal_message}"
            "However, you have the permissions to unbridge that room.\n\n"
            "To delete that portal completely and continue bridging, use "
            "`$cmdprefix+sp delete-and-continue`. To unbridge the portal "
            "without kicking Matrix users, use `$cmdprefix+sp unbridge-and-"
            "continue`. To cancel, use `$cmdprefix+sp cancel`"
        )
    evt.sender.command_status = {
        "next": confirm_bridge,
        "action": "Room bridging",
        "bridge_to_mxid": room_id,
        "tgid": portal.tgid,
        "peer_type": peer_type,
        "force_use_bot": force_use_bot,
    }
    return await evt.reply(
        "That Telegram chat has no existing portal. To confirm bridging the "
        "chat to this room, use `$cmdprefix+sp continue`"
    )


async def cleanup_old_portal_while_bridging(
    evt: CommandEvent, portal: po.Portal
) -> tuple[bool, Awaitable[None] | None]:
    if not portal.mxid:
        await evt.reply(
            "The portal seems to have lost its Matrix room between you"
            "calling `$cmdprefix+sp bridge` and this command.\n\n"
            "Continuing without touching previous Matrix room..."
        )
        return True, None
    elif evt.args[0] == "delete-and-continue":
        return True, portal.cleanup_portal("Portal deleted (moving to another room)", delete=False)
    elif evt.args[0] == "unbridge-and-continue":
        return True, portal.cleanup_portal(
            "Room unbridged (portal moving to another room)", puppets_only=True, delete=False
        )
    else:
        await evt.reply(
            "The chat you were trying to bridge already has a Matrix portal room.\n\n"
            "Please use `$cmdprefix+sp delete-and-continue` or `$cmdprefix+sp unbridge-and-"
            "continue` to either delete or unbridge the existing room (respectively) and "
            "continue with the bridging.\n\n"
            "If you changed your mind, use `$cmdprefix+sp cancel` to cancel."
        )
        return False, None


async def confirm_bridge(evt: CommandEvent) -> EventID | None:
    status = evt.sender.command_status
    try:
        portal = await po.Portal.get_by_tgid(status["tgid"], peer_type=status["peer_type"])
        bridge_to_mxid = status["bridge_to_mxid"]
    except KeyError:
        evt.sender.command_status = None
        return await evt.reply(
            "Fatal error: tgid or peer_type missing from command_status. "
            "This shouldn't happen unless you're messing with the command handler code."
        )

    is_logged_in = await evt.sender.is_logged_in() and not status["force_use_bot"]

    if "mxid" in status:
        if portal.peer_type != status["peer_type"]:
            evt.log.warning(
                "Portal %d in database has mismatching peer type %s (expected %s),"
                " trusting database as a room already existed",
                portal.tgid,
                portal.peer_type,
                status["peer_type"],
            )
            await evt.reply(
                "Mismatching peer type in command and portal table, "
                "trusting portal as room already existed"
            )
        ok, coro = await cleanup_old_portal_while_bridging(evt, portal)
        if not ok:
            return None
        elif coro:
            asyncio.create_task(coro)
            await evt.reply("Cleaning up previous portal room...")
    elif portal.mxid:
        evt.sender.command_status = None
        return await evt.reply(
            "The portal seems to have created a Matrix room between you "
            "calling `$cmdprefix+sp bridge` and this command.\n\n"
            "Please start over by calling the bridge command again."
        )
    elif evt.args[0] != "continue":
        return await evt.reply(
            "Please use `$cmdprefix+sp continue` to confirm the bridging or "
            "`$cmdprefix+sp cancel` to cancel."
        )
    elif portal.peer_type != status["peer_type"]:
        evt.log.warning(
            "Portal %d in database has mismatching peer type %s (expected %s),"
            " trusting new peer type as there's no existing room",
            portal.tgid,
            portal.peer_type,
            status["peer_type"],
        )
        await evt.reply(
            "Mismatching peer type in command and portal table, "
            "trusting you as portal room doesn't exist"
        )
        portal.peer_type = status["peer_type"]

    evt.sender.command_status = None
    async with portal._room_create_lock:
        await _locked_confirm_bridge(
            evt, portal=portal, room_id=bridge_to_mxid, is_logged_in=is_logged_in
        )


async def _locked_confirm_bridge(
    evt: CommandEvent, portal: po.Portal, room_id: RoomID, is_logged_in: bool
) -> EventID | None:
    user = evt.sender if is_logged_in else evt.tgbot
    try:
        entity = await user.client.get_entity(portal.peer)
    except Exception:
        evt.log.exception("Failed to get_entity(%s) for manual bridging.", portal.peer)
        if is_logged_in:
            return await evt.reply(
                "Failed to get info of telegram chat. You are logged in, are you in that chat?"
            )
        else:
            return await evt.reply(
                "Failed to get info of telegram chat. "
                "You're not logged in, is the relay bot in the chat?"
            )
    if isinstance(entity, (ChatForbidden, ChannelForbidden)):
        if is_logged_in:
            return await evt.reply("You don't seem to be in that chat.")
        else:
            return await evt.reply("The bot doesn't seem to be in that chat.")

    portal.mxid = room_id
    portal.by_mxid[portal.mxid] = portal
    (portal.title, portal.about, levels, portal.encrypted) = await get_initial_state(
        evt.az.intent, evt.room_id
    )
    portal.photo_id = ""
    await portal.save()
    await portal.update_bridge_info()

    asyncio.create_task(portal.update_matrix_room(user, entity, levels=levels))

    await warn_missing_power(levels, evt)

    return await evt.reply("Bridging complete. Portal synchronization should begin momentarily.")
