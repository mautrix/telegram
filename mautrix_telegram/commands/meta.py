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
from . import command_handler


@command_handler(needs_auth=False)
def cancel(evt):
    if evt.sender.command_status:
        action = evt.sender.command_status["action"]
        evt.sender.command_status = None
        return evt.reply(f"{action} cancelled.")
    else:
        return evt.reply("No ongoing command.")


@command_handler(needs_auth=False)
def unknown_command(evt):
    return evt.reply("Unknown command. Try `$cmdprefix+sp help` for help.")


@command_handler(needs_auth=False)
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
**login**  - Request an authentication code.  
**logout** - Log out from Telegram.  
**ping**   - Check if you're logged into Telegram.

#### Miscellaneous things
**search** [_-r|--remote_] <_query_> - Search your contacts or the Telegram servers for users.  
**sync** [`chats`|`contacts`|`me`]   - Synchronize your chat portals, contacts and/or own info.  
**ping-bot**                         - Get info of the message relay Telegram bot.  
**set-pl** <_level_> [_mxid_]        - Set a temporary power level without affecting Telegram.

#### Initiating chats
**pm** <_identifier_> - Open a private chat with the given Telegram user. The identifier is either
                        the internal user ID, the username or the phone number.  
**join** <_link_>     - Join a chat with an invite link.  
**create** [_type_]   - Create a Telegram chat of the given type for the current Matrix room. The
                        type is either `group`, `supergroup` or `channel` (defaults to `group`).

#### Portal management  
**upgrade**                 - Upgrade a normal Telegram group to a supergroup.  
**invite-link**             - Get a Telegram invite link to the current chat.  
**delete-portal**           - Remove all users from the current portal room and forget the portal.
                              Only works for group chats; to delete a private chat portal, simply
                              leave the room.  
**unbridge**                - Remove puppets from the current portal room and forget the portal.  
**bridge** [_id_]           - Bridge the current Matrix room to the Telegram chat with the given
                              ID. The ID must be the prefixed version that you get with the `/id`
                              command of the Telegram-side bot.  
**group-name** <_name_|`-`> - Change the username of a supergroup/channel. To disable, use a dash
                             (`-`) as the name.  
**clean-rooms**             - Clean up unused portal/management rooms.

**filter** <`whitelist`|`blacklist`> <_chat ID_> - Allow or disallow bridging a specific chat.  
**filter-mode** <`whitelist`|`blacklist`>      - Change whether the bridge will allow or disallow
                                                 bridging rooms by default.
"""
    return evt.reply(management_status + help)
