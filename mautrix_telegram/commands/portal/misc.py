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
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import GetFullChatRequest
from telethon.errors import (ChatAdminRequiredError, UsernameInvalidError,
                             UsernameNotModifiedError, UsernameOccupiedError, RPCError)

from mautrix.types import EventID

from ... import portal as po
from .. import command_handler, CommandEvent, SECTION_PORTAL_MANAGEMENT, SECTION_MISC
from .util import user_has_power_level


@command_handler(needs_admin=False, needs_puppeting=False, needs_auth=False,
                 help_section=SECTION_MISC,
                 help_text="Fetch Matrix room state to ensure the bridge has up-to-date info.")
async def sync_state(evt: CommandEvent) -> EventID:
    portal = po.Portal.get_by_mxid(evt.room_id)
    if not portal:
        return await evt.reply("This is not a portal room.")
    elif not await user_has_power_level(evt.room_id, evt.az.intent, evt.sender, "bridge"):
        return await evt.reply(f"You do not have the permissions to synchronize this room.")

    await portal.main_intent.get_joined_members(portal.mxid)
    await evt.reply("Synchronization complete")


@command_handler(needs_admin=False, needs_puppeting=False, needs_auth=False,
                 help_section=SECTION_MISC)
async def sync_full(evt: CommandEvent) -> EventID:
    portal = po.Portal.get_by_mxid(evt.room_id)
    if not portal:
        return await evt.reply("This is not a portal room.")

    if len(evt.args) > 0 and evt.args[0] == "--usebot" and evt.sender.is_admin:
        src = evt.tgbot
    else:
        src = evt.tgbot if await evt.sender.needs_relaybot(portal) else evt.sender

    try:
        if portal.peer_type == "channel":
            res = await src.client(GetFullChannelRequest(portal.peer))
        elif portal.peer_type == "chat":
            res = await src.client(GetFullChatRequest(portal.tgid))
        else:
            return await evt.reply("This is not a channel or chat portal.")
    except (ValueError, RPCError):
        return await evt.reply("Failed to get portal info from Telegram.")

    await portal.update_matrix_room(src, res.full_chat)
    return await evt.reply("Portal synced successfully.")


@command_handler(name="id", needs_admin=False, needs_puppeting=False, needs_auth=False,
                 help_section=SECTION_MISC,
                 help_text="Get the ID of the Telegram chat where this room is bridged.")
async def get_id(evt: CommandEvent) -> EventID:
    portal = po.Portal.get_by_mxid(evt.room_id)
    if not portal:
        return await evt.reply("This is not a portal room.")
    tgid = portal.tgid
    if portal.peer_type == "chat":
        tgid = -tgid
    elif portal.peer_type == "channel":
        tgid = f"-100{tgid}"
    await evt.reply(f"This room is bridged to Telegram chat ID `{tgid}`.")


@command_handler(help_section=SECTION_PORTAL_MANAGEMENT,
                 help_text="Get a Telegram invite link to the current chat.")
async def invite_link(evt: CommandEvent) -> EventID:
    portal = po.Portal.get_by_mxid(evt.room_id)
    if not portal:
        return await evt.reply("This is not a portal room.")

    if portal.peer_type == "user":
        return await evt.reply("You can't invite users to private chats.")

    try:
        link = await portal.get_invite_link(evt.sender)
        return await evt.reply(f"Invite link to {portal.title}: {link}")
    except ValueError as e:
        return await evt.reply(e.args[0])
    except ChatAdminRequiredError:
        return await evt.reply("You don't have the permission to create an invite link.")


@command_handler(help_section=SECTION_PORTAL_MANAGEMENT,
                 help_text="Upgrade a normal Telegram group to a supergroup.")
async def upgrade(evt: CommandEvent) -> EventID:
    portal = po.Portal.get_by_mxid(evt.room_id)
    if not portal:
        return await evt.reply("This is not a portal room.")
    elif portal.peer_type == "channel":
        return await evt.reply("This is already a supergroup or a channel.")
    elif portal.peer_type == "user":
        return await evt.reply("You can't upgrade private chats.")

    try:
        await portal.upgrade_telegram_chat(evt.sender)
        return await evt.reply(f"Group upgraded to supergroup. New ID: -100{portal.tgid}")
    except ChatAdminRequiredError:
        return await evt.reply("You don't have the permission to upgrade this group.")
    except ValueError as e:
        return await evt.reply(e.args[0])


@command_handler(help_section=SECTION_PORTAL_MANAGEMENT,
                 help_args="<_name_|`-`>",
                 help_text="Change the username of a supergroup/channel. "
                           "To disable, use a dash (`-`) as the name.")
async def group_name(evt: CommandEvent) -> EventID:
    if len(evt.args) == 0:
        return await evt.reply("**Usage:** `$cmdprefix+sp group-name <name/->`")

    portal = po.Portal.get_by_mxid(evt.room_id)
    if not portal:
        return await evt.reply("This is not a portal room.")
    elif portal.peer_type != "channel":
        return await evt.reply("Only channels and supergroups have usernames.")

    try:
        await portal.set_telegram_username(evt.sender,
                                           evt.args[0] if evt.args[0] != "-" else "")
        if portal.username:
            return await evt.reply(f"Username of channel changed to {portal.username}.")
        else:
            return await evt.reply(f"Channel is now private.")
    except ChatAdminRequiredError:
        return await evt.reply(
            "You don't have the permission to set the username of this channel.")
    except UsernameNotModifiedError:
        if portal.username:
            return await evt.reply("That is already the username of this channel.")
        else:
            return await evt.reply("This channel is already private")
    except UsernameOccupiedError:
        return await evt.reply("That username is already in use.")
    except UsernameInvalidError:
        return await evt.reply("Invalid username")
