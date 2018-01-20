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
import re

from .user import User
from .portal import Portal
from .commands import CommandHandler


class MatrixHandler:
    def __init__(self, context):
        self.az, self.db, log, self.config = context
        self.log = log.getChild("mx")
        self.commands = CommandHandler(context)

        alias_format = self.config.get("bridge.alias_template", "telegram_{}").format("(.+)")
        hs = self.config["homeserver"]["domain"]
        self.localpart_regex = re.compile(f"@{alias_format}:{hs}")

        self.az.matrix_event_handler(self.handle_event)

    def is_puppet(self, mxid):
        match = self.localpart_regex.match(mxid)
        return True if match else False

    def handle_invite(self, room, user, inviter):
        if user == self.az.bot_mxid:
            self.az.intent.join_room(room)
            return
        tgid = self.get_puppet(user)
        if tgid:
            # TODO handle puppet invite
            self.log.debug(f"{inviter} invited puppet for {tgid} to {room}")
            return
        # These can probably be ignored
        self.log.debug(f"{inviter} invited {user} to {room}")

    def handle_part(self, room, user):
        self.log.debug(f"{user} left {room}")

    def is_management(self, room):
        memberships = self.az.intent.get_room_members(room)
        return [membership["state_key"] for membership in memberships["chunk"] if
                membership["content"]["membership"] == "join"]

    def is_command(self, message):
        text = message.get("body", "")
        prefix = self.config["bridge.commands.prefix"]
        is_command = text.startswith(prefix)
        if is_command:
            text = text[len(prefix) + 1:]
        return is_command, text

    def handle_message(self, room, sender, message):
        self.log.debug(f"{sender} sent {message} to ${room}")

        is_command, text = self.is_command(message)
        sender = User.get_by_mxid(sender)

        portal = Portal.get_by_mxid(room)
        if portal and not is_command:
            portal.handle_matrix_message(sender, message)
            return

        if message["msgtype"] != "m.text":
            return

        is_management = len(self.is_management(room)) == 2
        if is_command or is_management:
            try:
                command, arguments = text.split(" ", 1)
                args = arguments.split(" ")
            except ValueError:
                # Not enough values to unpack, i.e. no arguments
                command = text
                args = []
            self.commands.handle(room, sender, command, args, is_management, is_portal=portal is not None)

    def filter_matrix_event(self, event):
        return event["sender"] == self.az.bot_mxid or self.is_puppet(event["sender"])

    def handle_event(self, evt):
        if self.filter_matrix_event(evt):
            return
        self.log.debug("Received event: %s", evt)
        type = evt["type"]
        content = evt.get("content", {})
        if type == "m.room.member":
            membership = content.get("membership", {})
            if membership == "invite":
                self.handle_invite(evt["room_id"], evt["state_key"], evt["sender"])
            elif membership == "leave":
                self.handle_part(evt["room_id"], evt["state_key"])
            elif membership == "join":
                pass
        elif type == "m.room.message":
            self.handle_message(evt["room_id"], evt["sender"], content)
