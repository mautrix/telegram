# -*- coding: future_fstrings -*-
# mautrix-telegram - A Matrix-Telegram puppeting bridge
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
from telethon_aio.errors import *
from mautrix_appservice import MatrixRequestError

from .. import portal as po
from . import command_handler


@command_handler()
async def invite_link(evt):
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


async def _has_access_to(room, intent, sender, event, default=50):
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


async def _get_portal_and_check_permission(evt, permission, action=None, allow_that=False):
    room_id = evt.args[0] if len(evt.args) > 0 and allow_that else evt.room_id

    portal = po.Portal.get_by_mxid(room_id)
    if not portal:
        that_this = "This" if room_id == evt.room_id else "That"
        return await evt.reply(f"{that_this} is not a portal room."), False

    if not _has_access_to(portal.mxid, portal.main_intent, evt.sender, permission):
        action = action or f"{permission.replace('_', ' ')}s"
        return await evt.reply(f"You do not have the permissions to {action}."), False
    return portal, True


def _get_portal_murder_function(action, room_id, function, command, completed_message):
    async def post_confirm(confirm):
        confirm.sender.command_status = None
        if len(confirm.args) > 0 and confirm.args[0] == f"confirm-{command}":
            await function()
            if confirm.room_id != room_id:
                return await confirm.reply(completed_message)
        else:
            return await confirm.reply(f"{action} cancelled.")

    return {
        "next": post_confirm,
        "action": action,
    }


@command_handler()
async def delete_portal(evt):
    portal, ok = await _get_portal_and_check_permission(evt, "delete_portal")
    if not ok:
        return

    evt.sender.command_status = _get_portal_murder_function("Portal deletion", portal.mxid,
                                                            portal.cleanup_and_delete, "delete",
                                                            "Portal successfully deleted.")
    return await evt.reply("Please confirm deletion of portal "
                           f"[{portal.alias or portal.mxid}](https://matrix.to/#/{portal.mxid}) "
                           f"to Telegram chat \"{portal.title}\" "
                           "by typing `$cmdprefix+sp confirm-delete`")


@command_handler()
async def unbridge(evt):
    portal, ok = await _get_portal_and_check_permission(evt, "unbridge_room", allow_that=False)
    if not ok:
        return

    evt.sender.command_status = _get_portal_murder_function("Room unbridging", portal.mxid,
                                                            portal.unbridge, "unbridge",
                                                            "Room successfully unbridged.")
    return await evt.reply(f"Please confirm unbridging chat \"{portal.title}\" from room "
                           f"[{portal.alias or portal.mxid}](https://matrix.to/#/{portal.mxid}) "
                           "by typing `$cmdprefix+sp confirm-unbridge`")


async def _get_initial_state(evt):
    state = await evt.az.intent.get_room_state(evt.room_id)
    title = None
    about = None
    levels = None
    for event in state:
        if event["type"] == "m.room.name":
            title = event["content"]["name"]
        elif event["type"] == "m.room.topic":
            about = event["content"]["topic"]
        elif event["type"] == "m.room.power_levels":
            levels = event["content"]
    return title, about, levels


def _check_power_levels(levels, bot_mxid):
    try:
        if levels["users"][bot_mxid] < 100:
            raise ValueError()
    except (TypeError, KeyError, ValueError):
        return (f"Please give [the bridge bot](https://matrix.to/#/{bot_mxid}) a power level of "
                "100 before creating a Telegram chat.")

    for user, level in levels["users"].items():
        if level >= 100 and user != bot_mxid:
            return (f"Please make sure only the bridge bot has power level above 99 before "
                    f"creating a Telegram chat.\n\n"
                    f"Use power level 95 instead of 100 for admins.")


@command_handler()
async def create(evt):
    type = evt.args[0] if len(evt.args) > 0 else "group"
    if type not in {"chat", "group", "supergroup", "channel"}:
        return await evt.reply(
            "**Usage:** `$cmdprefix+sp create ['group'/'supergroup'/'channel']`")

    if po.Portal.get_by_mxid(evt.room_id):
        return await evt.reply("This is already a portal room.")

    title, about, levels = await _get_initial_state(evt)
    if not title:
        return await evt.reply("Please set a title before creating a Telegram chat.")

    power_level_error = _check_power_levels(levels, evt.az.bot_mxid)
    if power_level_error:
        return await evt.reply(power_level_error)

    supergroup = type == "supergroup"
    type = {
        "supergroup": "channel",
        "channel": "channel",
        "chat": "chat",
        "group": "chat",
    }[type]

    portal = po.Portal(tgid=None, mxid=evt.room_id, title=title, about=about, peer_type=type)
    try:
        await portal.create_telegram_chat(evt.sender, supergroup=supergroup)
    except ValueError as e:
        portal.delete()
        return await evt.reply(e.args[0])
    return await evt.reply(f"Telegram chat created. ID: {portal.tgid}")


@command_handler()
async def upgrade(evt):
    portal = po.Portal.get_by_mxid(evt.room_id)
    if not portal:
        return await evt.reply("This is not a portal room.")
    elif portal.peer_type == "channel":
        return await evt.reply("This is already a supergroup or a channel.")
    elif portal.peer_type == "user":
        return await evt.reply("You can't upgrade private chats.")

    try:
        await portal.upgrade_telegram_chat(evt.sender)
        return await evt.reply(f"Group upgraded to supergroup. New ID: {portal.tgid}")
    except ChatAdminRequiredError:
        return await evt.reply("You don't have the permission to upgrade this group.")
    except ValueError as e:
        return await evt.reply(e.args[0])


@command_handler()
async def group_name(evt):
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
