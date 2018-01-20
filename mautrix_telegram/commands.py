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
from contextlib import contextmanager
import markdown

command_handlers = {}


def command_handler(func):
    command_handlers[func.__name__] = func


class CommandHandler:
    def __init__(self, context):
        self.appserv, self.db, log, self.config = context
        self.log = log.getChild("commands")
        self.command_prefix = self.config["bridge.commands.prefix"]
        self._room_id = None

    def handle(self, room, sender, command, args, is_management, is_portal):
        with self.handler(sender, room, command) as handle_command:
            handle_command(self, sender, args, is_management, is_portal)

    @contextmanager
    def handler(self, sender, room, command):
        self._room_id = room
        try:
            command = command_handlers[command]
        except KeyError:
            if sender.command_status and "next" in sender.command_status:
                command = sender.command_status["next"]
            else:
                command = command_handlers["unknown_command"]
        yield command
        self._room_id = None

    def reply(self, message, allow_html=False, render_markdown=True):
        if not self._room_id:
            raise AttributeError("the reply function can only be used from within"
                                 "the `CommandHandler.run` context manager")

        message = message.replace("$cmdprefix", self.command_prefix)
        html = None
        if render_markdown:
            html = markdown.markdown(message, safe_mode="escape" if allow_html else False)
        elif allow_html:
            html = message
        self.appserv.api.send_message_event(self._room_id, "m.room.message", {
            "msgtype": "m.notice",
            "body": message,
            "format": "org.matrix.custom.html" if html else None,
            "formatted_body": html or None,
        })

    @command_handler
    def cancel(self, sender, args, is_management, is_portal):
        if sender.command_status:
            sender.command_status = None
            return self.reply(f"{sender.command_status.action} cancelled.")
        else:
            return self.reply("No ongoing command.")

    @command_handler
    def unknown_command(self, sender, args, is_management, is_portal):
        if is_management:
            return self.reply("Unknown command. Try `help` for help.")
        else:
            return self.reply("Unknown command. Try `$cmdprefix help` for help.")

    @command_handler
    def help(self, sender, args, is_management, is_portal):
        if is_management:
            management_status = ("This is a management room: prefixing commands"
                                 "with `$cmdprefix` is not required.\n")
        elif is_portal:
            management_status = ("**This is a portal room**: you must always"
                                 "prefix commands with `$cmdprefix`.\n"
                                 "Management commands will not be sent to Telegram.")
        else:
            management_status = ("**This is not a management room**: you must"
                                 "prefix commands with `$cmdprefix`.\n")
        help = """
_**Generic bridge commands**: commands for using the bridge that aren't related to Telegram._  
**help** - Show this help message.  
**cancel** - Cancel an ongoing action (such as login).  

_**Telegram actions**: commands for using the bridge to interact with Telegram._  
**login** <_phone_> - Request an authentication code.  
**logout** - Log out from Telegram.  
**search** [_-r|--remote_] <_query_> - Search your contacts or the Telegram servers for users.  
**create** <_group/channel_> [_room ID_] - Create a Telegram chat of the given type for a Matrix room.
                                           If the room ID is not specified, a chat for the current room is created.  
**upgrade** - Upgrade a normal Telegram group to a supergroup.

_**Temporary commands**: commands that will be replaced with more Matrix-y actions later._  
**pm** <_id_> - Open a private chat with the given Telegram user ID.

_**Debug commands**: commands to help in debugging the bridge. Disabled by default._  
**api** <_method_> <_args_> - Call a Telegram API method. Args is always a single JSON object.
"""
        return self.reply(management_status + help)
