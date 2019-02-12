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
from typing import Dict, Optional

from telethon.errors import UsernameInvalidError, UsernameNotModifiedError, UsernameOccupiedError
from telethon.tl.functions.account import UpdateUsernameRequest

from mautrix_telegram.commands import command_handler, CommandEvent, SECTION_AUTH


@command_handler(needs_auth=True,
                 help_section=SECTION_AUTH,
                 help_args="<_new username_>",
                 help_text="Change your Telegram username.")
async def username(evt: CommandEvent) -> Optional[Dict]:
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
