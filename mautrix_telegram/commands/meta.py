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
from . import command_handler


@command_handler()
def cancel(evt):
    if evt.sender.command_status:
        action = evt.sender.command_status["action"]
        evt.sender.command_status = None
        return evt.reply(f"{action} cancelled.")
    else:
        return evt.reply("No ongoing command.")


@command_handler()
def unknown_command(evt):
    return evt.reply("Unknown command. Try `$cmdprefix+sp help` for help.")


@command_handler()
def help(evt):
    if evt.is_management:
        management_status = ("This is a management room: prefixing commands "
                             "with `$cmdprefix` is not required.\n")
    elif evt.is_portal:
        management_status = ("**This is a portal room**: you must always "
                             "prefix commands with `$cmdprefix`.\n"
                             "Management commands will not be sent to Telegram.")
    else:
        management_status = ("**This is not a management room**: you must "
                             "prefix commands with `$cmdprefix`.\n")
    help = """\n
#### Generic bridge commands
**help**   - Show this help message.  
**cancel** - Cancel an ongoing action (such as login).

#### Authentication
**login** <_phone_> - Request an authentication code.  
**logout**          - Log out from Telegram.  
**ping**            - Check if you're logged into Telegram.

#### Initiating chats
**search** [_-r|--remote_] <_query_> - Search your contacts or the Telegram servers for users.  
**pm** <_identifier_>                - Open a private chat with the given Telegram user. The
                                       identifier is either the internal user ID, the username or
                                       the phone number.  
**join** <_link_>                    - Join a chat with an invite link.  
**create** [_type_]                  - Create a Telegram chat of the given type for the current
                                       Matrix room. The type is either `group`, `supergroup` or
                                       `channel` (defaults to `group`).

#### Portal management  
**ping-bot**                - Get info of the message relay Telegram bot.
**upgrade**                 - Upgrade a normal Telegram group to a supergroup.  
**invite-link**             - Get a Telegram invite link to the current chat.  
**delete-portal**           - Forget the current portal room. Only works for group chats; to delete
                             a private chat portal, simply leave the room.  
**group-name** <_name_|`-`> - Change the username of a supergroup/channel. To disable, use a dash
                             (`-`) as the name.  
**clean-rooms**             - Clean up unused portal/management rooms.
"""
    return evt.reply(management_status + help)
