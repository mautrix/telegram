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

from datetime import datetime, timedelta
import re

from telethon.errors import (
    ChatAdminRequiredError,
    RPCError,
    UsernameInvalidError,
    UsernameNotModifiedError,
    UsernameOccupiedError,
)
from telethon.helpers import add_surrogate
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import GetExportedChatInvitesRequest, GetFullChatRequest
from telethon.tl.types import (
    ChatInviteExported,
    InputMessageEntityMentionName,
    InputUserSelf,
    MessageEntityMention,
    TypeInputPeer,
    TypeInputUser,
)
from telethon.tl.types.messages import ExportedChatInvites

from mautrix.types import EventID

from ... import formatter as fmt, portal as po, puppet as pu
from .. import SECTION_MISC, SECTION_PORTAL_MANAGEMENT, CommandEvent, command_handler
from .util import user_has_power_level


@command_handler(
    needs_admin=False,
    needs_puppeting=False,
    needs_auth=False,
    help_section=SECTION_MISC,
    help_text="Fetch Matrix room state to ensure the bridge has up-to-date info.",
)
async def sync_state(evt: CommandEvent) -> EventID:
    portal = await po.Portal.get_by_mxid(evt.room_id)
    if not portal:
        return await evt.reply("This is not a portal room.")
    elif not await user_has_power_level(evt.room_id, evt.az.intent, evt.sender, "bridge"):
        return await evt.reply(f"You do not have the permissions to synchronize this room.")

    await portal.main_intent.get_joined_members(portal.mxid)
    await evt.reply("Synchronization complete")


@command_handler(
    needs_admin=False, needs_puppeting=False, needs_auth=False, help_section=SECTION_MISC
)
async def sync_full(evt: CommandEvent) -> EventID:
    portal = await po.Portal.get_by_mxid(evt.room_id)
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


@command_handler(
    name="id",
    needs_admin=False,
    needs_puppeting=False,
    needs_auth=False,
    help_section=SECTION_MISC,
    help_text="Get the ID of the Telegram chat where this room is bridged.",
)
async def get_id(evt: CommandEvent) -> EventID:
    portal = await po.Portal.get_by_mxid(evt.room_id)
    if not portal:
        return await evt.reply("This is not a portal room.")
    tgid = portal.tgid
    if portal.peer_type == "chat":
        tgid = -tgid
    elif portal.peer_type == "channel":
        tgid = f"-100{tgid}"
    await evt.reply(f"This room is bridged to Telegram chat ID `{tgid}`.")


invite_link_usage = (
    "**Usage:** `$cmdprefix+sp invite-link "
    "[--uses=<amount>] [--expire=<delta>] [--request-needed] -- [title]`"
    "\n\n"
    "* `--uses`: the number of times the invite link can be used."
    "            Defaults to unlimited.\n"
    "* `--expire`: the duration after which the link will expire."
    "              A number suffixed with d(ay), h(our), m(inute) or s(econd)\n"
    "* `--request-needed`: should the link require admins to approve joins?\n"
    "* `title`: a description of the link (only shown to admins)."
)


def _parse_flag(args: list[str]) -> tuple[str, str]:
    arg = args.pop(0).lower()
    if arg == "--":
        return "", ""
    value = ""
    if arg.startswith("--"):
        value_start = arg.find("=")
        if value_start > 0:
            flag = arg[2:value_start]
            value = arg[value_start + 1 :]
        else:
            flag = arg[2:]
            if arg not in ("request", "request-needed"):
                value = args.pop(0).lower()
    elif arg.startswith("-"):
        flag = arg[1]
        if len(arg) > 3 and arg[2] == "=":
            value = arg[3:]
        elif arg != "r":
            value = args.pop(0).lower()
    else:
        raise ValueError("invalid flag")
    return flag, value


delta_regex = re.compile(
    "([0-9]+)(w(?:eek)?|d(?:ay)?|h(?:our)?|m(?:in(?:ute)?)?|s(?:ec(?:ond)?)?)"
)


def _parse_delta(value: str) -> timedelta | None:
    match = delta_regex.fullmatch(value)
    if not match:
        return None
    number = int(match.group(1))
    unit = match.group(2)[0]
    if unit == "w":
        return timedelta(weeks=number)
    elif unit == "d":
        return timedelta(days=number)
    elif unit == "h":
        return timedelta(hours=number)
    elif unit == "m":
        return timedelta(minutes=number)
    elif unit == "s":
        return timedelta(seconds=number)
    else:
        return None


@command_handler(
    help_section=SECTION_PORTAL_MANAGEMENT,
    help_text="Get a Telegram invite link to the current chat.",
    help_args="[--uses=<amount>] [--expire=<time delta, e.g. 1d>] [--request-needed] -- [title]",
)
async def invite_link(evt: CommandEvent) -> EventID:
    if not evt.is_portal:
        return await evt.reply("This is not a portal room.")

    # TODO once we switch to Python 3.9 minimum, use argparse with exit_on_error=False
    uses = None
    expire = None
    request_needed = False
    while evt.args:
        try:
            flag, value = _parse_flag(evt.args)
        except (ValueError, IndexError):
            return await evt.reply(invite_link_usage)
        if not flag:
            break
        elif flag in ("uses", "u"):
            try:
                uses = int(value)
            except ValueError:
                await evt.reply("The number of uses must be an integer")
        elif flag in ("expire", "e"):
            expire_delta = _parse_delta(value)
            if not expire_delta:
                await evt.reply("Invalid format for expiry time delta")
            expire = datetime.now() + expire_delta
        elif flag in ("request", "request-needed", "r"):
            request_needed = True
    title = " ".join(evt.args)

    if evt.portal.peer_type == "user":
        return await evt.reply("You can't invite users to private chats.")

    try:
        link = await evt.portal.get_invite_link(
            evt.sender, uses=uses, expire=expire, request_needed=request_needed, title=title
        )
        return await evt.reply(f"Invite link to {evt.portal.title}: {link}")
    except ValueError as e:
        return await evt.reply(e.args[0])
    except ChatAdminRequiredError:
        return await evt.reply("You don't have the permission to create an invite link.")


async def _format_invite_link(link: ChatInviteExported) -> str:
    desc = f"* {link.link}"
    if link.title:
        desc += f" - {link.title}"
    if link.expire_date:
        desc += f"  \n  Expires at {link.expire_date.isoformat()}"
    if link.usage_limit:
        desc += f"  \n  Used {link.usage or 0} out of {link.usage_limit} times"
    elif link.usage:
        desc += f"  \n  Used {link.usage} times"
    else:
        desc += "  \n  Never used"
    if link.request_needed:
        desc += "  \n  Join requests enabled - using link requires admin approval"
    return desc


async def _hacky_find_mention(evt: CommandEvent) -> TypeInputUser | TypeInputPeer | None:
    if len(evt.args) == 0:
        return None
    text, entities = await fmt.matrix_to_telegram(
        evt.sender.client, text=evt.content.body, html=evt.content.formatted_body
    )
    for entity in entities:
        if isinstance(entity, MessageEntityMention):
            admin_username = add_surrogate(text)[entity.offset + 1 : entity.offset + entity.length]
            return await evt.sender.client.get_input_entity(admin_username)
        elif isinstance(entity, InputMessageEntityMentionName):
            return entity.user_id
    return None


@command_handler(
    help_section=SECTION_PORTAL_MANAGEMENT,
    help_text="List existing Telegram invite links to the current chat.",
    help_args="[creator]",
)
async def list_invite_links(evt: CommandEvent) -> EventID:
    admin_id = InputUserSelf()
    try:
        admin_id = await _hacky_find_mention(evt) or InputUserSelf()
    except Exception:
        pass
    resp: ExportedChatInvites = await evt.sender.client(
        GetExportedChatInvitesRequest(
            peer=await evt.portal.get_input_entity(evt.sender),
            admin_id=admin_id,
            limit=100,
        )
    )
    if resp.count == 0:
        if isinstance(admin_id, InputUserSelf):
            return await evt.reply("You haven't created any invite links to the current chat")
        else:
            return await evt.reply("That user hasn't created any invite links to the current chat")
    formatted_links = "\n".join([await _format_invite_link(link) for link in resp.invites])
    if isinstance(admin_id, InputUserSelf):
        await evt.reply(f"Your links to this chat:\n\n{formatted_links}")
    else:
        puppet = await pu.Puppet.get_by_peer(admin_id)
        await evt.reply(
            f"[{puppet.displayname}](https://matrix.to/#/{puppet.mxid})'s links to this chat:\n\n"
            f"{formatted_links}"
        )


@command_handler(
    help_section=SECTION_PORTAL_MANAGEMENT,
    help_text="Upgrade a normal Telegram group to a supergroup.",
)
async def upgrade(evt: CommandEvent) -> EventID:
    portal = await po.Portal.get_by_mxid(evt.room_id)
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


@command_handler(
    help_section=SECTION_PORTAL_MANAGEMENT,
    help_args="<_name_|`-`>",
    help_text=(
        "Change the username of a supergroup/channel. To disable, use a dash (`-`) as the name."
    ),
)
async def group_name(evt: CommandEvent) -> EventID:
    if len(evt.args) == 0:
        return await evt.reply("**Usage:** `$cmdprefix+sp group-name <name/->`")

    portal = await po.Portal.get_by_mxid(evt.room_id)
    if not portal:
        return await evt.reply("This is not a portal room.")
    elif portal.peer_type != "channel":
        return await evt.reply("Only channels and supergroups have usernames.")

    try:
        await portal.set_telegram_username(evt.sender, evt.args[0] if evt.args[0] != "-" else "")
        if portal.username:
            return await evt.reply(f"Username of channel changed to {portal.username}.")
        else:
            return await evt.reply(f"Channel is now private.")
    except ChatAdminRequiredError:
        return await evt.reply(
            "You don't have the permission to set the username of this channel."
        )
    except UsernameNotModifiedError:
        if portal.username:
            return await evt.reply("That is already the username of this channel.")
        else:
            return await evt.reply("This channel is already private")
    except UsernameOccupiedError:
        return await evt.reply("That username is already in use.")
    except UsernameInvalidError:
        return await evt.reply("Invalid username")
