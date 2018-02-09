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
import logging

from matrix_client.errors import MatrixRequestError

from .user import User
from .portal import Portal
from .puppet import Puppet
from .commands import CommandHandler


class MatrixHandler:
    log = logging.getLogger("mau.mx")

    def __init__(self, context):
        self.az, self.db, self.config, _ = context
        self.commands = CommandHandler(context)

        self.az.matrix_event_handler(self.handle_event)

    async def init_as_bot(self):
        self.az.intent.set_display_name(
            self.config.get("appservice.bot_displayname", "Telegram bridge bot"))

    async def handle_puppet_invite(self, room, puppet, inviter):
        self.log.debug(f"{inviter} invited puppet for {puppet.tgid} to {room}")
        if not inviter.logged_in:
            await puppet.intent.error_and_leave(
                room, text="Please log in before inviting Telegram puppets.")
            return
        portal = Portal.get_by_mxid(room)
        if portal:
            if portal.peer_type == "user":
                await puppet.intent.error_and_leave(
                    room, text="You can not invite additional users to private chats.")
                return
            await portal.invite_telegram(inviter, puppet)
            await puppet.intent.join_room(room)
            return
        try:
            members = await self.az.intent.get_room_members(room)
        except MatrixRequestError:
            members = []
        if self.az.intent.mxid not in members:
            if len(members) > 1:
                await puppet.intent.error_and_leave(room, text=None, html=(
                    f"Please invite "
                    + f"<a href='https://matrix.to/#/{self.az.intent.mxid}'>the bridge bot</a> "
                    + f"first if you want to create a Telegram chat."))
                return

            await puppet.intent.join_room(room)
            portal = Portal.get_by_tgid(puppet.tgid, inviter.tgid, "user")
            if portal.mxid:
                try:
                    await puppet.intent.invite(portal.mxid, inviter.mxid)
                    await puppet.intent.send_notice(room, text=None, html=(
                        "You already have a private chat with me: "
                        + f"<a href='https://matrix.to/#/{portal.mxid}'>"
                        + "Link to room"
                        + "</a>"))
                    await puppet.intent.leave_room(room)
                    return
                except MatrixRequestError:
                    pass
            portal.mxid = room
            portal.save()
            await puppet.intent.send_notice(room, "Portal to private chat created.")
        else:
            await puppet.intent.join_room(room)
            await puppet.intent.send_notice(room, "This puppet will remain inactive until a"
                                                  "Telegram chat is created for this room.")

    async def handle_invite(self, room, user, inviter):
        inviter = User.get_by_mxid(inviter)
        if not inviter.whitelisted:
            return
        elif user == self.az.bot_mxid:
            await self.az.intent.join_room(room)
            return

        puppet = Puppet.get_by_mxid(user)
        if puppet:
            await self.handle_puppet_invite(room, puppet, inviter)
            return

        user = User.get_by_mxid(user, create=False)
        portal = Portal.get_by_mxid(room)
        if user and user.has_full_access and portal:
            await portal.invite_telegram(inviter, user)
            return

        # The rest can probably be ignored
        self.log.debug(f"{inviter} invited {user} to {room}")

    async def handle_join(self, room, user):
        user = User.get_by_mxid(user)

        portal = Portal.get_by_mxid(room)
        if not portal:
            return

        if not user.whitelisted:
            await portal.main_intent.kick(room, user.mxid,
                                          "You are not whitelisted on this Telegram bridge.")
            return
        elif not user.logged_in:
            # TODO[waiting-for-bots] once we have bot support, this won't be needed.
            await portal.main_intent.kick(room, user.mxid,
                                          "You are not logged into this Telegram bridge.")
            return

        self.log.debug(f"{user} joined {room}")
        # TODO join Telegram chat if applicable

    async def handle_part(self, room, user, sender):
        self.log.debug(f"{user} left {room}")

        sender = User.get_by_mxid(sender, create=False)

        portal = Portal.get_by_mxid(room)
        if not portal:
            return

        puppet = Puppet.get_by_mxid(user)
        if sender and puppet:
            await portal.leave_matrix(puppet, sender)

        user = User.get_by_mxid(user, create=False)
        if user and user.logged_in:
            await portal.leave_matrix(user, sender)

    def is_command(self, message):
        text = message.get("body", "")
        prefix = self.config["bridge.command_prefix"]
        is_command = text.startswith(prefix)
        if is_command:
            text = text[len(prefix) + 1:]
        return is_command, text

    async def handle_message(self, room, sender, message, event_id):
        self.log.debug(f"{sender} sent {message} to ${room}")

        is_command, text = self.is_command(message)
        sender = User.get_by_mxid(sender)

        portal = Portal.get_by_mxid(room)
        if sender.has_full_access and portal and not is_command:
            await portal.handle_matrix_message(sender, message, event_id)
            return

        if message["msgtype"] != "m.text":
            return

        try:
            is_management = len(await self.az.intent.get_room_members(room)) == 2
        except MatrixRequestError:
            # The AS bot is not in the room.
            return

        if is_command or is_management:
            try:
                command, arguments = text.split(" ", 1)
                args = arguments.split(" ")
            except ValueError:
                # Not enough values to unpack, i.e. no arguments
                command = text
                args = []
            await self.commands.handle(room, sender, command, args, is_management,
                                       is_portal=portal is not None)

    async def handle_redaction(self, room, sender, event_id):
        portal = Portal.get_by_mxid(room)
        sender = User.get_by_mxid(sender)
        if sender.has_full_access and portal:
            await portal.handle_matrix_deletion(sender, event_id)

    async def handle_power_levels(self, room, sender, new, old):
        portal = Portal.get_by_mxid(room)
        sender = User.get_by_mxid(sender)
        if sender.has_full_access and portal:
            await portal.handle_matrix_power_levels(sender, new["users"], old["users"])

    async def handle_room_meta(self, type, room, sender, content):
        portal = Portal.get_by_mxid(room)
        sender = User.get_by_mxid(sender)
        if sender.has_full_access and portal:
            handler, content_key = {
                "m.room.name": (portal.handle_matrix_title, "name"),
                "m.room.topic": (portal.handle_matrix_about, "topic"),
                "m.room.avatar": (portal.handle_matrix_avatar, "url"),
            }[type]
            if content_key not in content:
                # FIXME handle
                pass
                await handler(sender, content[content_key])

    def filter_matrix_event(self, event):
        return (event["sender"] == self.az.bot_mxid
                or Puppet.get_id_from_mxid(event["sender"]) is not None)

    async def handle_event(self, evt):
        if self.filter_matrix_event(evt):
            return
        self.log.debug("Received event: %s", evt)
        type = evt["type"]
        content = evt.get("content", {})
        if type == "m.room.member":
            membership = content.get("membership", "")
            if membership == "invite":
                await self.handle_invite(evt["room_id"], evt["state_key"], evt["sender"])
            elif membership == "leave":
                await self.handle_part(evt["room_id"], evt["state_key"], evt["sender"])
            elif membership == "join":
                await self.handle_join(evt["room_id"], evt["state_key"])
        elif type == "m.room.message":
            await self.handle_message(evt["room_id"], evt["sender"], content, evt["event_id"])
        elif type == "m.room.redaction":
            await self.handle_redaction(evt["room_id"], evt["sender"], evt["redacts"])
        elif type == "m.room.power_levels":
            await self.handle_power_levels(evt["room_id"], evt["sender"], evt["content"],
                                           evt["prev_content"])
        elif type == "m.room.name" or type == "m.room.avatar" or type == "m.room.topic":
            await self.handle_room_meta(type, evt["room_id"], evt["sender"], evt["content"])
