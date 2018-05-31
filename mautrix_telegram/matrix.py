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
import logging

from mautrix_appservice import MatrixRequestError

from .user import User
from .portal import Portal
from .puppet import Puppet
from .commands import CommandHandler


class MatrixHandler:
    log = logging.getLogger("mau.mx")

    def __init__(self, context):
        self.az, self.db, self.config, _, self.tgbot = context
        self.commands = CommandHandler(context)

        self.az.matrix_event_handler(self.handle_event)

    async def init_as_bot(self):
        await self.az.intent.set_display_name(
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
        if self.az.bot_mxid not in members:
            if len(members) > 1:
                await puppet.intent.error_and_leave(room, text=None, html=(
                    f"Please invite "
                    f"<a href='https://matrix.to/#/{self.az.bot_mxid}'>the bridge bot</a> "
                    f"first if you want to create a Telegram chat."))
                return

            await puppet.intent.join_room(room)
            portal = Portal.get_by_tgid(puppet.tgid, inviter.tgid, "user")
            if portal.mxid:
                try:
                    await puppet.intent.invite(portal.mxid, inviter.mxid)
                    await puppet.intent.send_notice(room, text=None, html=(
                        "You already have a private chat with me: "
                        f"<a href='https://matrix.to/#/{portal.mxid}'>"
                        "Link to room"
                        "</a>"))
                    await puppet.intent.leave_room(room)
                    return
                except MatrixRequestError:
                    pass
            portal.mxid = room
            portal.save()
            inviter.register_portal(portal)
            await puppet.intent.send_notice(room, "Portal to private chat created.")
        else:
            await puppet.intent.join_room(room)
            await puppet.intent.send_notice(room, "This puppet will remain inactive until a "
                                                  "Telegram chat is created for this room.")

    async def handle_invite(self, room, user, inviter):
        self.log.debug(f"{inviter} invited {user} to {room}")
        inviter = await User.get_by_mxid(inviter).ensure_started()
        if user == self.az.bot_mxid:
            await self.az.intent.join_room(room)
            if not inviter.whitelisted:
                await self.az.intent.send_notice(
                    room, text=None,
                    html="You are not whitelisted to use this bridge.<br/><br/>"
                         "If you are the owner of this bridge, see the "
                         "<code>bridge.permissions</code> section in your config file.")
                await self.az.intent.leave_room(room)
            return
        elif not inviter.whitelisted:
            return

        puppet = Puppet.get_by_mxid(user)
        if puppet:
            await self.handle_puppet_invite(room, puppet, inviter)
            return

        user = User.get_by_mxid(user, create=False)
        if not user:
            return
        await user.ensure_started()
        portal = Portal.get_by_mxid(room)
        if user and user.has_full_access and portal:
            await portal.invite_telegram(inviter, user)
            return

        # The rest can probably be ignored

    async def handle_join(self, room, user, event_id):
        user = await User.get_by_mxid(user).ensure_started()

        portal = Portal.get_by_mxid(room)
        if not portal:
            return

        if not user.relaybot_whitelisted:
            await portal.main_intent.kick(room, user.mxid,
                                          "You are not whitelisted on this Telegram bridge.")
            return
        elif not user.logged_in and not portal.has_bot:
            await portal.main_intent.kick(room, user.mxid,
                                          "This chat does not have a bot relaying "
                                          "messages for unauthenticated users.")
            return

        self.log.debug(f"{user} joined {room}")
        if user.logged_in or portal.has_bot:
            await portal.join_matrix(user, event_id)

    async def handle_part(self, room, user, sender, event_id):
        self.log.debug(f"{user} left {room}")

        sender = User.get_by_mxid(sender, create=False)
        if not sender:
            return
        await sender.ensure_started()

        portal = Portal.get_by_mxid(room)
        if not portal:
            return

        puppet = Puppet.get_by_mxid(user)
        if sender and puppet:
            await portal.leave_matrix(puppet, sender, event_id)

        user = User.get_by_mxid(user, create=False)
        if not user:
            return
        await user.ensure_started()
        if user.logged_in or portal.has_bot:
            await portal.leave_matrix(user, sender, event_id)

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
        sender = await User.get_by_mxid(sender).ensure_started()
        if not sender.relaybot_whitelisted:
            return

        portal = Portal.get_by_mxid(room)
        if not is_command and portal and (sender.logged_in or portal.has_bot):
            await portal.handle_matrix_message(sender, message, event_id)
            return

        if not sender.whitelisted or message["msgtype"] != "m.text":
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
        sender = await User.get_by_mxid(sender).ensure_started()
        if not sender.relaybot_whitelisted:
            return

        portal = Portal.get_by_mxid(room)
        if not portal:
            return

        await portal.handle_matrix_deletion(sender, event_id)

    async def handle_power_levels(self, room, sender, new, old):
        portal = Portal.get_by_mxid(room)
        sender = await User.get_by_mxid(sender).ensure_started()
        if sender.has_full_access and portal:
            await portal.handle_matrix_power_levels(sender, new["users"], old["users"])

    async def handle_room_meta(self, type, room, sender, content):
        portal = Portal.get_by_mxid(room)
        sender = await User.get_by_mxid(sender).ensure_started()
        if sender.has_full_access and portal:
            handler, content_key = {
                "m.room.name": (portal.handle_matrix_title, "name"),
                "m.room.topic": (portal.handle_matrix_about, "topic"),
                "m.room.avatar": (portal.handle_matrix_avatar, "url"),
            }[type]
            if content_key not in content:
                return
            await handler(sender, content[content_key])

    async def handle_room_pin(self, room, sender, new_events, old_events):
        portal = Portal.get_by_mxid(room)
        sender = await User.get_by_mxid(sender).ensure_started()
        if sender.has_full_access and portal:
            events = new_events - old_events
            if len(events) > 0:
                # New event pinned, set that as pinned in Telegram.
                await portal.handle_matrix_pin(sender, events.pop())
            elif len(new_events) == 0:
                # All pinned events removed, remove pinned event in Telegram.
                await portal.handle_matrix_pin(sender, None)

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
            prev_content = evt.get("unsigned", {}).get("prev_content", {})
            membership = content.get("membership", "")
            prev_membership = prev_content.get("membership", "leave")
            if membership == prev_membership:
                # TODO handle displayname/avatar changes
                pass
            elif membership == "invite":
                await self.handle_invite(evt["room_id"], evt["state_key"], evt["sender"])
            elif prev_membership == "join" and membership == "leave":
                await self.handle_part(evt["room_id"], evt["state_key"], evt["sender"],
                                       evt["event_id"])
            elif membership == "join":
                await self.handle_join(evt["room_id"], evt["state_key"], evt["event_id"])
        elif type in ("m.room.message", "m.sticker"):
            if type != "m.room.message":
                content["msgtype"] = type
            await self.handle_message(evt["room_id"], evt["sender"], content, evt["event_id"])
        elif type == "m.room.redaction":
            await self.handle_redaction(evt["room_id"], evt["sender"], evt["redacts"])
        elif type == "m.room.power_levels":
            await self.handle_power_levels(evt["room_id"], evt["sender"], evt["content"],
                                           evt["prev_content"])
        elif type in ("m.room.name", "m.room.avatar", "m.room.topic"):
            await self.handle_room_meta(type, evt["room_id"], evt["sender"], evt["content"])
        elif type == "m.room.pinned_events":
            new_events = set(evt["content"]["pinned"])
            try:
                old_events = set(evt["unsigned"]["prev_content"]["pinned"])
            except KeyError:
                old_events = set()
            await self.handle_room_pin(evt["room_id"], evt["sender"], new_events, old_events)
