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
from telethon.errors import *
from telethon.tl.types import User as TLUser
from telethon.tl.functions.messages import ImportChatInviteRequest, CheckChatInviteRequest
from telethon.tl.functions.channels import JoinChannelRequest

from .. import puppet as pu, portal as po
from . import command_handler


@command_handler()
async def search(evt):
    if len(evt.args) == 0:
        return await evt.reply("**Usage:** `$cmdprefix+sp search [-r|--remote] <query>`")

    force_remote = False
    if evt.args[0] in {"-r", "--remote"}:
        force_remote = True
        evt.args.pop(0)

    query = " ".join(evt.args)
    if force_remote and len(query) < 5:
        return await evt.reply("Minimum length of query for remote search is 5 characters.")

    results, remote = await evt.sender.search(query, force_remote)

    if not results:
        if len(query) < 5 and remote:
            return await evt.reply("No local results. "
                                   "Minimum length of remote query is 5 characters.")
        return await evt.reply("No results 3:")

    reply = []
    if remote:
        reply += ["**Results from Telegram server:**", ""]
    else:
        reply += ["**Results in contacts:**", ""]
    reply += [(f"* [{puppet.displayname}](https://matrix.to/#/{puppet.mxid}): "
               + f"{puppet.id} ({similarity}% match)")
              for puppet, similarity in results]

    # TODO somehow show remote channel results when joining by alias is possible?

    return await evt.reply("\n".join(reply))


@command_handler()
async def pm(evt):
    if len(evt.args) == 0:
        return await evt.reply("**Usage:** `$cmdprefix+sp pm <user identifier>`")

    user = await evt.sender.client.get_entity(evt.args[0])
    if not user:
        return await evt.reply("User not found.")
    elif not isinstance(user, TLUser):
        return await evt.reply("That doesn't seem to be a user.")
    portal = po.Portal.get_by_entity(user, evt.sender.tgid)
    await portal.create_matrix_room(evt.sender, user, [evt.sender.mxid])
    return await evt.reply(
        f"Created private chat room with {pu.Puppet.get_displayname(user, False)}")


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


@command_handler(needs_admin=True)
async def delete_portal(evt):
    room_id = evt.args[0] if len(evt.args) > 0 else evt.room_id

    portal = po.Portal.get_by_mxid(room_id)
    if not portal:
        that_this = "This" if room_id == evt.room_id else "That"
        return await evt.reply(f"{that_this} is not a portal room.")

    async def post_confirm(_, confirm):
        evt.sender.command_status = None
        if len(confirm.args) > 0 and confirm.args[0] == "confirm-delete":
            await portal.cleanup_and_delete()
            if confirm.room_id != room_id:
                return await confirm.reply("Portal successfully deleted.")
        else:
            return await confirm.reply("Portal deletion cancelled.")

    evt.sender.command_status = {
        "next": post_confirm,
        "action": "Portal deletion",
    }
    return await evt.reply("Please confirm deletion of portal "
                           + f"[{room_id}](https://matrix.to/#/{room_id}) "
                           + f"to Telegram chat \"{portal.title}\" "
                           + "by typing `$cmdprefix+sp confirm-delete`")


@command_handler()
async def join(evt):
    if len(evt.args) == 0:
        return await evt.reply("**Usage:** `$cmdprefix+sp join <invite link>`")

    regex = re.compile(r"(?:https?://)?t(?:elegram)?\.(?:dog|me)(?:joinchat/)?/(.+)")
    arg = regex.match(evt.args[0])
    if not arg:
        return await evt.reply("That doesn't look like a Telegram invite link.")
    arg = arg.group(1)
    if arg.startswith("joinchat/"):
        invite_hash = arg[len("joinchat/"):]
        try:
            await evt.sender.client(CheckChatInviteRequest(invite_hash))
        except InviteHashInvalidError:
            return await evt.reply("Invalid invite link.")
        except InviteHashExpiredError:
            return await evt.reply("Invite link expired.")
        try:
            updates = evt.sender.client(ImportChatInviteRequest(invite_hash))
        except UserAlreadyParticipantError:
            return await evt.reply("You are already in that chat.")
    else:
        channel = await evt.sender.client.get_entity(arg)
        if not channel:
            return await evt.reply("Channel/supergroup not found.")
        updates = await evt.sender.client(JoinChannelRequest(channel))
    for chat in updates.chats:
        portal = po.Portal.get_by_entity(chat)
        if portal.mxid:
            await portal.create_matrix_room(evt.sender, chat, [evt.sender.mxid])
            return await evt.reply(f"Created room for {portal.title}")
        else:
            await portal.invite_matrix([evt.sender.mxid])
            return await evt.reply(f"Invited you to portal of {portal.title}")


@command_handler()
async def create(evt):
    type = evt.args[0] if len(evt.args) > 0 else "group"
    if type not in {"chat", "group", "supergroup", "channel"}:
        return await evt.reply(
            "**Usage:** `$cmdprefix+sp create ['group'/'supergroup'/'channel']`")

    if po.Portal.get_by_mxid(evt.room_id):
        return await evt.reply("This is already a portal room.")

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
    if not title:
        return await evt.reply("Please set a title before creating a Telegram chat.")
    elif (not levels or not levels["users"] or evt.az.intent.mxid not in levels["users"] or
          levels["users"][evt.az.intent.mxid] < 100):
        return await evt.reply(f"Please give "
                               + f"[the bridge bot](https://matrix.to/#/{evt.az.intent.mxid})"
                               + f" a power level of 100 before creating a Telegram chat.")
    else:
        for user, level in levels["users"].items():
            if level >= 100 and user != evt.az.intent.mxid:
                return await evt.reply(
                    f"Please make sure only the bridge bot has power level above"
                    + f"99 before creating a Telegram chat.\n\n"
                    + f"Use power level 95 instead of 100 for admins.")

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
