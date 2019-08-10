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
from typing import Optional

from telethon.errors import (UsernameInvalidError, UsernameNotModifiedError, UsernameOccupiedError,
                             HashInvalidError, AuthKeyError, FirstNameInvalidError)
from telethon.tl.types import Authorization
from telethon.tl.functions.account import (UpdateUsernameRequest, GetAuthorizationsRequest,
                                           ResetAuthorizationRequest, UpdateProfileRequest)

from mautrix.types import EventID

from .. import command_handler, CommandEvent, SECTION_AUTH


@command_handler(needs_auth=True,
                 help_section=SECTION_AUTH,
                 help_args="<_new username_>",
                 help_text="Change your Telegram username.")
async def username(evt: CommandEvent) -> EventID:
    if len(evt.args) == 0:
        return await evt.reply("**Usage:** `$cmdprefix+sp username <new username>`")
    if evt.sender.is_bot:
        return await evt.reply("Bots can't set their own username.")
    new_name = evt.args[0]
    if new_name == "-":
        new_name = ""
    try:
        await evt.sender.client(UpdateUsernameRequest(username=new_name))
    except UsernameInvalidError:
        return await evt.reply("Invalid username. Usernames must be between 5 and 30 alphanumeric "
                               "characters.")
    except UsernameNotModifiedError:
        return await evt.reply("That is your current username.")
    except UsernameOccupiedError:
        return await evt.reply("That username is already in use.")
    await evt.sender.update_info()
    if not evt.sender.username:
        await evt.reply("Username removed")
    else:
        await evt.reply(f"Username changed to {evt.sender.username}")


@command_handler(needs_auth=True, help_section=SECTION_AUTH, help_args="<_new displayname_>",
                 help_text="Change your Telegram displayname.")
async def displayname(evt: CommandEvent) -> EventID:
    if len(evt.args) == 0:
        return await evt.reply("**Usage:** `$cmdprefix+sp displayname <new displayname>`")
    if evt.sender.is_bot:
        return await evt.reply("Bots can't set their own displayname.")

    first_name, last_name = ((evt.args[0], "")
                             if len(evt.args) == 1
                             else (" ".join(evt.args[:-1]), evt.args[-1]))
    try:
        await evt.sender.client(UpdateProfileRequest(first_name=first_name, last_name=last_name))
    except FirstNameInvalidError:
        return await evt.reply("Invalid first name")
    await evt.sender.update_info()
    return await evt.reply("Displayname updated")


def _format_session(sess: Authorization) -> str:
    return (f"**{sess.app_name} {sess.app_version}**  \n"
            f"  **Platform:** {sess.device_model} {sess.platform} {sess.system_version}  \n"
            f"  **Active:** {sess.date_active} (created {sess.date_created})  \n"
            f"  **From:** {sess.ip} - {sess.region}, {sess.country}")


@command_handler(needs_auth=True,
                 help_section=SECTION_AUTH,
                 help_args="<`list`|`terminate`> [_hash_]",
                 help_text="View or delete other Telegram sessions.")
async def session(evt: CommandEvent) -> EventID:
    if len(evt.args) == 0:
        return await evt.reply("**Usage:** `$cmdprefix+sp session <list|terminate> [hash]`")
    elif evt.sender.is_bot:
        return await evt.reply("Bots can't manage their sessions")
    cmd = evt.args[0].lower()
    if cmd == "list":
        res = await evt.sender.client(GetAuthorizationsRequest())
        session_list = res.authorizations
        current = [s for s in session_list if s.current][0]
        current_text = _format_session(current)
        other_text = "\n".join(f"* {_format_session(sess)}  \n"
                               f"  **Hash:** {sess.hash}"
                               for sess in session_list if not sess.current)
        return await evt.reply(f"### Current session\n"
                               f"{current_text}\n"
                               f"\n"
                               f"### Other active sessions\n"
                               f"{other_text}")
    elif cmd == "terminate" and len(evt.args) > 1:
        try:
            session_hash = int(evt.args[1])
        except ValueError:
            return await evt.reply("Hash must be an integer")
        try:
            ok = await evt.sender.client(ResetAuthorizationRequest(hash=session_hash))
        except HashInvalidError:
            return await evt.reply("Invalid session hash.")
        except AuthKeyError as e:
            if e.message == "FRESH_RESET_AUTHORISATION_FORBIDDEN":
                return await evt.reply("New sessions can't terminate other sessions. "
                                       "Please wait a while.")
            raise
        if ok:
            return await evt.reply("Session terminated successfully.")
        else:
            return await evt.reply("Session not found.")
    else:
        return await evt.reply("**Usage:** `$cmdprefix+sp session <list|terminate> [hash]`")
