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
from matrix_client.errors import MatrixRequestError

from .user import User
from .portal import Portal
from .puppet import Puppet
from .commands import CommandHandler


class MatrixHandler:
    def __init__(self, context):
        self.az, self.db, log, self.config = context
        self.log = log.getChild("mx")
        self.commands = CommandHandler(context)

        self.az.matrix_event_handler(self.handle_event)
        self.az.intent.set_display_name(
            self.config.get("appservice.bot_displayname", "Telegram bridge bot"))

    def is_puppet(self, mxid):
        match = Puppet.mxid_regex.match(mxid)
        return True if match else False

    def get_puppet(self, mxid):
        match = Puppet.mxid_regex.match(mxid)
        if not match:
            return None
        return Puppet.get(int(match.group(1)))

    def handle_puppet_invite(self, room, puppet, inviter):
        self.log.debug(f"{inviter} invited puppet for {puppet.tgid} to {room}")
        if not inviter.logged_in:
            puppet.intent.error_and_leave(
                room, text="Please log in before inviting Telegram puppets.")
            return
        portal = Portal.get_by_mxid(room)
        if portal:
            if portal.peer_type == "user":
                puppet.intent.error_and_leave(
                    room, text="You can not invite additional users to private chats.")
                return
            portal.invite_telegram(inviter, puppet)
            puppet.intent.join_room(room)
            return
        try:
            members = self.az.intent.get_room_members(room)
        except MatrixRequestError:
            members = []
        if self.az.intent.mxid not in members:
            if len(members) > 1:
                puppet.intent.error_and_leave(room, text=None, html=(
                    f"Please invite "
                    f"<a href='https://matrix.to/#/{self.az.intent.mxid}'>the bridge bot</a> "
                    f"first if you want to create a Telegram chat."))
                return

            puppet.intent.join_room(room)
            existing_portal = Portal.get_by_tgid(puppet.tgid, "user")
            if existing_portal:
                try:
                    puppet.intent.invite(existing_portal.mxid, inviter.mxid)
                    puppet.intent.send_notice(room, text=None, html=(
                        "You already have a private chat with me: "
                        f"<a href='https://matrix.to/#/{existing_portal.mxid}'>"
                        "Link to room"
                        "</a>"))
                    puppet.intent.leave_room(room)
                    return
                except MatrixRequestError:
                    existing_portal.delete()

            portal = Portal(tgid=puppet.tgid, peer_type="user", mxid=room)
            portal.save()
            puppet.intent.send_notice(room, "Portal to private chat created.")
        else:
            puppet.intent.join_room(room)
            puppet.intent.send_notice(room, "This puppet will remain inactive until a Telegram "
                                            "chat is created for this room.")

    def handle_invite(self, room, user, inviter):
        inviter = User.get_by_mxid(inviter)
        if not inviter.whitelisted:
            return
        elif user == self.az.bot_mxid:
            self.az.intent.join_room(room)
            return
        puppet = self.get_puppet(user)
        if puppet:
            self.handle_puppet_invite(room, puppet, inviter)
            return
        # These can probably be ignored
        self.log.debug(f"{inviter} invited {user} to {room}")

    def handle_join(self, room, user):
        user = User.get_by_mxid(user)

        portal = Portal.get_by_mxid(room)
        if not portal:
            return

        if not user.whitelisted:
            portal.main_intent.kick(room, user.mxid,
                                    "You are not whitelisted on this Telegram bridge.")
            return
        elif not user.logged_in:
            portal.main_intent.kick(room, user.mxid,
                                    "You are not logged into this Telegram bridge.")
            return

        self.log.debug(f"{user} joined {room}")
        # TODO join Telegram chat if applicable

    def handle_part(self, room, user):
        self.log.debug(f"{user} left {room}")
        # user = User.get_by_mxid(user, create=False)

    def is_command(self, message):
        text = message.get("body", "")
        prefix = self.config["bridge.command_prefix"]
        is_command = text.startswith(prefix)
        if is_command:
            text = text[len(prefix) + 1:]
        return is_command, text

    def handle_message(self, room, sender, message, event_id):
        self.log.debug(f"{sender} sent {message} to ${room}")

        is_command, text = self.is_command(message)
        sender = User.get_by_mxid(sender)

        portal = Portal.get_by_mxid(room)
        if sender.has_full_access and portal and not is_command:
            portal.handle_matrix_message(sender, message, event_id)
            return

        if message["msgtype"] != "m.text":
            return

        is_management = len(self.az.intent.get_room_members(room)) == 2
        if is_command or is_management:
            try:
                command, arguments = text.split(" ", 1)
                args = arguments.split(" ")
            except ValueError:
                # Not enough values to unpack, i.e. no arguments
                command = text
                args = []
            self.commands.handle(room, sender, command, args, is_management,
                                 is_portal=portal is not None)

    def handle_redaction(self, room, sender, event_id):
        portal = Portal.get_by_mxid(room)
        sender = User.get_by_mxid(sender)
        if sender.has_full_access and portal:
            portal.handle_matrix_deletion(sender, event_id)

    def handle_power_levels(self, room, sender, new, old):
        portal = Portal.get_by_mxid(room)
        sender = User.get_by_mxid(sender)
        if sender.has_full_access and portal:
            sender = User.get_by_mxid(sender)
            portal.handle_matrix_power_levels(sender, new["users"], old["users"])

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
                self.handle_join(evt["room_id"], evt["state_key"])
        elif type == "m.room.message":
            self.handle_message(evt["room_id"], evt["sender"], content, evt["event_id"])
        elif type == "m.room.redaction":
            self.handle_redaction(evt["room_id"], evt["sender"], evt["redacts"])
        elif type == "m.room.power_levels":
            self.handle_power_levels(evt["room_id"], evt["sender"], evt["content"],
                                     evt["prev_content"])
