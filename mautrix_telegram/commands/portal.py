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
import asyncio

from telethon_aio.errors import *
from mautrix_appservice import MatrixRequestError

from .. import portal as po
from . import command_handler, CommandEvent


@command_handler(needs_admin=True, needs_auth=False, name="set-pl")
async def set_power_level(evt: CommandEvent):
    try:
        level = int(evt.args[0])
    except KeyError:
        return await evt.reply("**Usage:** `$cmdprefix+sp set-power <level> [mxid]`")
    except ValueError:
        return await evt.reply("The level must be an integer.")
    levels = await evt.az.intent.get_power_levels(evt.room_id)
    mxid = evt.args[1] if len(evt.args) > 1 else evt.sender.mxid
    levels["users"][mxid] = level
    try:
        await evt.az.intent.set_power_levels(evt.room_id, levels)
    except MatrixRequestError:
        evt.log.exception("Failed to set power level.")
        return await evt.reply("Failed to set power level.")


@command_handler()
async def invite_link(evt: CommandEvent):
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


async def _get_portal_and_check_permission(evt, permission, action=None):
    room_id = evt.args[0] if len(evt.args) > 0 else evt.room_id

    portal = po.Portal.get_by_mxid(room_id)
    if not portal:
        that_this = "This" if room_id == evt.room_id else "That"
        return await evt.reply(f"{that_this} is not a portal room."), False

    if not await _has_access_to(portal.mxid, evt.az.intent, evt.sender, permission):
        action = action or f"{permission.replace('_', ' ')}s"
        return await evt.reply(f"You do not have the permissions to {action} that portal."), False
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
async def delete_portal(evt: CommandEvent):
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
async def unbridge(evt: CommandEvent):
    portal, ok = await _get_portal_and_check_permission(evt, "unbridge_room")
    if not ok:
        return

    evt.sender.command_status = _get_portal_murder_function("Room unbridging", portal.mxid,
                                                            portal.unbridge, "unbridge",
                                                            "Room successfully unbridged.")
    return await evt.reply(f"Please confirm unbridging chat \"{portal.title}\" from room "
                           f"[{portal.alias or portal.mxid}](https://matrix.to/#/{portal.mxid}) "
                           "by typing `$cmdprefix+sp confirm-unbridge`")


@command_handler()
async def bridge(evt: CommandEvent):
    if len(evt.args) == 0:
        return await evt.reply("**Usage:** "
                               "`$cmdprefix+sp bridge <Telegram chat ID> [Matrix room ID]`")
    room_id = evt.args[1] if len(evt.args) > 1 else evt.room_id
    that_this = "This" if room_id == evt.room_id else "That"

    portal = po.Portal.get_by_mxid(room_id)
    if portal:
        return await evt.reply(f"{that_this} room is already a portal room.")

    if not await _has_access_to(room_id, evt.az.intent, evt.sender, "bridge"):
        return await evt.reply("You do not have the permissions to bridge that room.")

    levels = await evt.az.intent.get_power_levels(room_id)
    power_level_error = _check_power_levels(levels, evt.az.bot_mxid)
    if power_level_error:
        return await evt.reply(power_level_error)

    # The /id bot command provides the prefixed ID, so we assume
    tgid = evt.args[0]
    if tgid.startswith("-100"):
        tgid = int(tgid[4:])
        peer_type = "channel"
    elif tgid.startswith("-"):
        tgid = -int(tgid)
        peer_type = "chat"
    else:
        return await evt.reply("That doesn't seem like a prefixed Telegram chat ID.\n\n"
                               "If you did not get the ID using the `/id` bot command, please "
                               "prefix channel IDs with `-100` and normal group IDs with `-`.\n\n"
                               "Bridging private chats to existing rooms is not allowed.")

    portal = po.Portal.get_by_tgid(tgid, peer_type=peer_type)
    if portal.mxid:
        has_portal_message = (
            "That Telegram chat already has a portal at "
            f"[{portal.alias or portal.mxid}](https://matrix.to/#/{portal.mxid}). ")
        if not await _has_access_to(portal.mxid, evt.az.intent, evt.sender, "unbridge"):
            return await evt.reply(f"{has_portal_message}"
                                   "Additionally, you do not have the permissions to unbridge "
                                   "that room.")
        evt.sender.command_status = {
            "next": confirm_bridge,
            "action": "Room bridging",
            "mxid": portal.mxid,
            "bridge_to_mxid": room_id,
            "tgid": portal.tgid,
            "peer_type": portal.peer_type,
        }
        return await evt.reply(f"{has_portal_message}"
                               "However, you have the permissions to unbridge that room.\n\n"
                               "To delete that portal completely and continue bridging, use "
                               "`$cmdprefix+sp delete-and-continue`. To unbridge the portal "
                               "without kicking Matrix users, use `$cmdprefix+sp unbridge-and-"
                               "continue`. To cancel, use `$cmdprefix+sp cancel`")
    evt.sender.command_status = {
        "next": confirm_bridge,
        "action": "Room bridging",
        "bridge_to_mxid": room_id,
        "tgid": portal.tgid,
        "peer_type": portal.peer_type,
    }
    return await evt.reply("That Telegram chat has no existing portal. To confirm bridging the "
                           "chat to this room, use `$cmdprefix+sp continue`")


async def cleanup_old_portal_while_bridging(evt, portal):
    if not portal.mxid:
        await evt.reply("The portal seems to have lost its Matrix room between you"
                        "calling `$cmdprefix+sp bridge` and this command.\n\n"
                        "Continuing without touching previous Matrix room...")
        return True, None
    elif evt.args[0] == "delete-and-continue":
        return True, portal.cleanup_room(portal.main_intent, portal.mxid,
                                         message="Portal deleted (moving to another room)")
    elif evt.args[0] == "unbridge-and-continue":
        return True, portal.cleanup_room(portal.main_intent, portal.mxid,
                                         message="Room unbridged (portal moving to another room)",
                                         puppets_only=True)
    else:
        await evt.reply(
            "The chat you were trying to bridge already has a Matrix portal room.\n\n"
            "Please use `$cmdprefix+sp delete-and-continue` or `$cmdprefix+sp unbridge-and-"
            "continue` to either delete or unbridge the existing room (respectively) and "
            "continue with the bridging.\n\n"
            "If you changed your mind, use `$cmdprefix+sp cancel` to cancel.")
        return False, None


async def confirm_bridge(evt: CommandEvent):
    status = evt.sender.command_status
    try:
        portal = po.Portal.get_by_tgid(status["tgid"], peer_type=status["peer_type"])
        bridge_to_mxid = status["bridge_to_mxid"]
    except KeyError:
        evt.sender.command_status = None
        return await evt.reply("Fatal error: tgid or peer_type missing from command_status. "
                               "This shouldn't happen unless you're messing with the command "
                               "handler code.")
    if "mxid" in status:
        ok, coro = await cleanup_old_portal_while_bridging(evt, portal)
        if not ok:
            return
        elif coro:
            asyncio.ensure_future(coro, loop=evt.loop)
            await evt.reply("Cleaning up previous portal room...")
    elif portal.mxid:
        evt.sender.command_status = None
        return await evt.reply("The portal seems to have created a Matrix room between you "
                               "calling `$cmdprefix+sp bridge` and this command.\n\n"
                               "Please start over by calling the bridge command again.")
    elif evt.args[0] != "continue":
        return await evt.reply("Please use `$cmdprefix+sp continue` to confirm the bridging or "
                               "`$cmdprefix+sp cancel` to cancel.")

    portal.mxid = bridge_to_mxid
    portal.title, portal.about, levels = await _get_initial_state(evt)
    portal.photo_id = ""
    portal.save()

    entity = await evt.sender.client.get_entity(portal.peer)
    direct = False
    asyncio.ensure_future(portal.update_matrix_room(evt.sender, entity, direct, levels=levels),
                          loop=evt.loop)

    return await evt.reply("Bridging complete. Portal synchronization should begin momentarily.")


async def _get_initial_state(evt: CommandEvent):
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


def _check_power_levels(levels: dict, bot_mxid: str):
    try:
        if levels["users"][bot_mxid] < 100:
            raise ValueError()
    except (TypeError, KeyError, ValueError):
        return (f"Please give [the bridge bot](https://matrix.to/#/{bot_mxid}) a power level of "
                "100 before creating or bridging a Telegram chat.")

    for user, level in levels["users"].items():
        if level >= 100 and user != bot_mxid:
            return (f"Please make sure only the bridge bot has power level above 99 before "
                    f"creating or bridging a Telegram chat.\n\n"
                    f"Use power level 95 instead of 100 for admins.")


@command_handler()
async def create(evt: CommandEvent):
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
async def upgrade(evt: CommandEvent):
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
async def group_name(evt: CommandEvent):
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
