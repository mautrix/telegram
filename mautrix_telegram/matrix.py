# -*- coding: future_fstrings -*-
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
from typing import Dict, List, Match, Optional, Set, Tuple, TYPE_CHECKING
import logging
import asyncio
import time
import re

from mautrix_appservice import MatrixRequestError, IntentError

from .types import MatrixEvent, MatrixEventID, MatrixRoomID, MatrixUserID
from . import user as u, portal as po, puppet as pu, commands as com

if TYPE_CHECKING:
    from .context import Context

try:
    from prometheus_client import Histogram

    EVENT_TIME = Histogram("matrix_event", "Time spent processing Matrix events",
                           ["event_type"])
except ImportError:
    Histogram = None
    EVENT_TIME = None


class MatrixHandler:
    log = logging.getLogger("mau.mx")  # type: logging.Logger

    def __init__(self, context: 'Context') -> None:
        self.az, self.config, _, self.tgbot = context.core
        self.commands = com.CommandProcessor(context)  # type: com.CommandProcessor
        self.previously_typing = []  # type: List[MatrixUserID]

        self.az.matrix_event_handler(self.handle_event)

    async def init_as_bot(self) -> None:
        displayname = self.config["appservice.bot_displayname"]
        if displayname:
            try:
                await self.az.intent.set_display_name(
                    displayname if displayname != "remove" else "")
            except asyncio.TimeoutError:
                self.log.exception("TimeoutError when trying to set displayname")

        avatar = self.config["appservice.bot_avatar"]
        if avatar:
            try:
                await self.az.intent.set_avatar(avatar if avatar != "remove" else "")
            except asyncio.TimeoutError:
                self.log.exception("TimeoutError when trying to set avatar")

    async def handle_puppet_invite(self, room_id: MatrixRoomID, puppet: pu.Puppet, inviter: u.User
                                   ) -> None:
        intent = puppet.default_mxid_intent
        self.log.debug(f"{inviter} invited puppet for {puppet.tgid} to {room_id}")
        if not await inviter.is_logged_in():
            await intent.error_and_leave(
                room_id, text="Please log in before inviting Telegram puppets.")
            return
        portal = po.Portal.get_by_mxid(room_id)
        if portal:
            if portal.peer_type == "user":
                await intent.error_and_leave(
                    room_id, text="You can not invite additional users to private chats.")
                return
            await portal.invite_telegram(inviter, puppet)
            await intent.join_room(room_id)
            return
        try:
            members = await self.az.intent.get_room_members(room_id)
        except MatrixRequestError:
            members = []
        if self.az.bot_mxid not in members:
            if len(members) > 1:
                await intent.error_and_leave(room_id, text=None, html=(
                    f"Please invite "
                    f"<a href='https://matrix.to/#/{self.az.bot_mxid}'>the bridge bot</a> "
                    f"first if you want to create a Telegram chat."))
                return

            await intent.join_room(room_id)
            portal = po.Portal.get_by_tgid(puppet.tgid, inviter.tgid, "user")
            # TODO: if portal is None:
            if portal.mxid:
                try:
                    await intent.invite(portal.mxid, inviter.mxid)
                    await intent.send_notice(room_id, text=None, html=(
                        "You already have a private chat with me: "
                        f"<a href='https://matrix.to/#/{portal.mxid}'>"
                        "Link to room"
                        "</a>"))
                    await intent.leave_room(room_id)
                    return
                except MatrixRequestError:
                    pass
            portal.mxid = room_id
            portal.save()
            inviter.register_portal(portal)
            await intent.send_notice(room_id, "Portal to private chat created.")
        else:
            await intent.join_room(room_id)
            await intent.send_notice(room_id, "This puppet will remain inactive until a "
                                              "Telegram chat is created for this room.")

    async def accept_bot_invite(self, room_id: MatrixRoomID, inviter: u.User) -> None:
        tries = 0
        while tries < 5:
            try:
                await self.az.intent.join_room(room_id)
                break
            except (IntentError, MatrixRequestError):
                tries += 1
                wait_for_seconds = (tries + 1) * 10
                if tries < 5:
                    self.log.exception(f"Failed to join room {room_id} with bridge bot, "
                                       f"retrying in {wait_for_seconds} seconds...")
                    await asyncio.sleep(wait_for_seconds)
                else:
                    self.log.exception("Failed to join room {room}, giving up.")
                    return

        if not inviter.whitelisted:
            await self.az.intent.send_notice(
                room_id,
                text="You are not whitelisted to use this bridge.\n\n"
                     "If you are the owner of this bridge, see the "
                     "`bridge.permissions` section in your config file.",
                html="<p>You are not whitelisted to use this bridge.</p>"
                     "<p>If you are the owner of this bridge, see the "
                     "<code>bridge.permissions</code> section in your config file.</p>")
            await self.az.intent.leave_room(room_id)

        try:
            is_management = len(await self.az.intent.get_room_members(room_id)) == 2
        except MatrixRequestError:
            is_management = False
        cmd_prefix = self.commands.command_prefix
        text = html = "Hello, I'm a Telegram bridge bot. "
        if is_management and inviter.puppet_whitelisted and not await inviter.is_logged_in():
            text += f"Use `{cmd_prefix} help` for help or `{cmd_prefix} login` to log in."
            html += (f"Use <code>{cmd_prefix} help</code> for help"
                     f" or <code>{cmd_prefix} login</code> to log in.")
            pass
        else:
            text += f"Use `{cmd_prefix} help` for help."
            html += f"Use <code>{cmd_prefix} help</code> for help."
        await self.az.intent.send_notice(room_id, text=text, html=html)

    async def handle_invite(self, room_id: MatrixRoomID, user_id: MatrixUserID,
                            inviter_mxid: MatrixUserID) -> None:
        self.log.debug(f"{inviter_mxid} invited {user_id} to {room_id}")
        inviter = u.User.get_by_mxid(inviter_mxid)
        if inviter is None:
            self.log.exception("Failed to find user with Matrix ID {inviter_mxid}")
        await inviter.ensure_started()
        if user_id == self.az.bot_mxid:
            return await self.accept_bot_invite(room_id, inviter)
        elif not inviter.whitelisted:
            return

        puppet = pu.Puppet.get_by_mxid(user_id)
        if puppet:
            await self.handle_puppet_invite(room_id, puppet, inviter)
            return

        user = u.User.get_by_mxid(user_id, create=False)
        if not user:
            return
        await user.ensure_started()
        portal = po.Portal.get_by_mxid(room_id)
        if user and await user.has_full_access(allow_bot=True) and portal:
            await portal.invite_telegram(inviter, user)
            return

        # The rest can probably be ignored

    async def handle_join(self, room_id: MatrixRoomID, user_id: MatrixUserID,
                          event_id: MatrixEventID) -> None:
        user = await u.User.get_by_mxid(user_id).ensure_started()

        portal = po.Portal.get_by_mxid(room_id)
        if not portal:
            return

        if not user.relaybot_whitelisted:
            await portal.main_intent.kick(room_id, user.mxid,
                                          "You are not whitelisted on this Telegram bridge.")
            return
        elif not await user.is_logged_in() and not portal.has_bot:
            await portal.main_intent.kick(room_id, user.mxid,
                                          "This chat does not have a bot relaying "
                                          "messages for unauthenticated users.")
            return

        self.log.debug(f"{user} joined {room_id}")
        if await user.is_logged_in() or portal.has_bot:
            await portal.join_matrix(user, event_id)

    async def handle_part(self, room_id: MatrixRoomID, user_id: MatrixUserID,
                          sender_mxid: MatrixUserID, event_id: MatrixEventID) -> None:
        self.log.debug(f"{user_id} left {room_id}")

        sender = u.User.get_by_mxid(sender_mxid, create=False)
        if not sender:
            return
        await sender.ensure_started()

        portal = po.Portal.get_by_mxid(room_id)
        if not portal:
            return

        puppet = pu.Puppet.get_by_mxid(user_id)
        if puppet:
            if sender:
                await portal.kick_matrix(puppet, sender)
            return

        user = u.User.get_by_mxid(user_id, create=False)
        if not user:
            return
        await user.ensure_started()
        if await user.is_logged_in() or portal.has_bot:
            await portal.leave_matrix(user, sender, event_id)

    def is_command(self, message: Dict) -> Tuple[bool, str]:
        text = message.get("body", "")
        prefix = self.config["bridge.command_prefix"]
        is_command = text.startswith(prefix)
        if is_command:
            text = text[len(prefix) + 1:].lstrip()
        return is_command, text

    async def handle_message(self, room: MatrixRoomID, sender_id: MatrixUserID, message: Dict,
                             event_id: MatrixEventID) -> None:
        is_command, text = self.is_command(message)
        sender = await u.User.get_by_mxid(sender_id).ensure_started()
        if not sender.relaybot_whitelisted:
            self.log.debug(f"Ignoring message \"{message}\" from {sender} to {room}:"
                           " User is not whitelisted.")
            return
        self.log.debug(f"Received Matrix event \"{message}\" from {sender} in {room}")

        portal = po.Portal.get_by_mxid(room)
        if not is_command and portal and (await sender.is_logged_in() or portal.has_bot):
            await portal.handle_matrix_message(sender, message, event_id)
            return

        if not sender.whitelisted or message.get("msgtype", "m.unknown") != "m.text":
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
            await self.commands.handle(room, event_id, sender, command, args, is_management,
                                       is_portal=portal is not None)

    @staticmethod
    async def handle_redaction(room_id: MatrixRoomID, sender_mxid: MatrixUserID,
                               event_id: MatrixEventID) -> None:
        sender = await u.User.get_by_mxid(sender_mxid).ensure_started()
        if not sender.relaybot_whitelisted:
            return

        portal = po.Portal.get_by_mxid(room_id)
        if not portal:
            return

        await portal.handle_matrix_deletion(sender, event_id)

    @staticmethod
    async def handle_power_levels(room_id: MatrixRoomID, sender_mxid: MatrixUserID,
                                  new: Dict, old: Dict) -> None:
        portal = po.Portal.get_by_mxid(room_id)
        sender = await u.User.get_by_mxid(sender_mxid).ensure_started()
        if await sender.has_full_access(allow_bot=True) and portal:
            await portal.handle_matrix_power_levels(sender, new["users"], old["users"])

    @staticmethod
    async def handle_room_meta(evt_type: str, room_id: MatrixRoomID, sender_mxid: MatrixUserID,
                               content: dict) -> None:
        portal = po.Portal.get_by_mxid(room_id)
        sender = await u.User.get_by_mxid(sender_mxid).ensure_started()
        if await sender.has_full_access(allow_bot=True) and portal:
            handler, content_key = {
                "m.room.name": (portal.handle_matrix_title, "name"),
                "m.room.topic": (portal.handle_matrix_about, "topic"),
                "m.room.avatar": (portal.handle_matrix_avatar, "url"),
            }[evt_type]
            if content_key not in content:
                return
            await handler(sender, content[content_key])

    @staticmethod
    async def handle_room_pin(room_id: MatrixRoomID, sender_mxid: MatrixUserID,
                              new_events: Set[str], old_events: Set[str]) -> None:
        portal = po.Portal.get_by_mxid(room_id)
        sender = await u.User.get_by_mxid(sender_mxid).ensure_started()
        if await sender.has_full_access(allow_bot=True) and portal:
            events = new_events - old_events
            if len(events) > 0:
                # New event pinned, set that as pinned in Telegram.
                await portal.handle_matrix_pin(sender, MatrixEventID(events.pop()))
            elif len(new_events) == 0:
                # All pinned events removed, remove pinned event in Telegram.
                await portal.handle_matrix_pin(sender, None)

    @staticmethod
    async def handle_room_upgrade(room_id: MatrixRoomID, new_room_id: MatrixRoomID) -> None:
        portal = po.Portal.get_by_mxid(room_id)
        if portal:
            await portal.handle_matrix_upgrade(new_room_id)

    @staticmethod
    async def handle_name_change(room_id: MatrixRoomID, user_id: MatrixUserID, displayname: str,
                                 prev_displayname: str, event_id: MatrixEventID) -> None:
        portal = po.Portal.get_by_mxid(room_id)
        if not portal or not portal.has_bot:
            return

        user = await u.User.get_by_mxid(user_id).ensure_started()
        if await user.needs_relaybot(portal):
            await portal.name_change_matrix(user, displayname, prev_displayname, event_id)

    @staticmethod
    def parse_read_receipts(content: Dict) -> Dict[MatrixUserID, MatrixEventID]:
        return {user_id: event_id
                for event_id, receipts in content.items()
                for user_id in receipts.get("m.read", {})}

    @staticmethod
    async def handle_read_receipts(room_id: MatrixRoomID,
                                   receipts: Dict[MatrixUserID, MatrixEventID]) -> None:
        portal = po.Portal.get_by_mxid(room_id)
        if not portal:
            return

        for user_id, event_id in receipts.items():
            user = await u.User.get_by_mxid(user_id).ensure_started()
            if not await user.is_logged_in():
                continue
            await portal.mark_read(user, event_id)

    @staticmethod
    async def handle_presence(user_id: MatrixUserID, presence: str) -> None:
        user = await u.User.get_by_mxid(user_id).ensure_started()
        if not await user.is_logged_in():
            return
        await user.set_presence(presence == "online")

    async def handle_typing(self, room_id: MatrixRoomID, now_typing: List[MatrixUserID]) -> None:
        portal = po.Portal.get_by_mxid(room_id)
        if not portal:
            return

        for user_id in set(self.previously_typing + now_typing):
            is_typing = user_id in now_typing
            was_typing = user_id in self.previously_typing
            if is_typing and was_typing:
                continue

            user = await u.User.get_by_mxid(user_id).ensure_started()
            if not await user.is_logged_in():
                continue

            await portal.set_typing(user, is_typing)

        self.previously_typing = now_typing

    def filter_matrix_event(self, event: MatrixEvent) -> bool:
        sender = event.get("sender", None)
        if not sender:
            return False
        return (sender == self.az.bot_mxid
                or pu.Puppet.get_id_from_mxid(sender) is not None)

    async def try_handle_event(self, evt: MatrixEvent) -> None:
        try:
            await self.handle_event(evt)
        except Exception:
            self.log.exception("Error handling manually received Matrix event")

    async def handle_event(self, evt: MatrixEvent) -> None:
        if self.filter_matrix_event(evt):
            return
        start_time = time.time()
        self.log.debug("Received event: %s", evt)
        evt_type = evt.get("type", "m.unknown")  # type: str
        room_id = evt.get("room_id", None)  # type: Optional[MatrixRoomID]
        event_id = evt.get("event_id", None)  # type: Optional[MatrixEventID]
        sender = evt.get("sender", None)  # type: Optional[MatrixUserID]
        content = evt.get("content", {})  # type: Dict
        if evt_type == "m.room.member":
            state_key = evt["state_key"]  # type: MatrixUserID
            prev_content = evt.get("unsigned", {}).get("prev_content", {})  # type: Dict
            membership = content.get("membership", "")  # type: str
            prev_membership = prev_content.get("membership", "leave")  # type: str
            if membership == prev_membership:
                match = re.compile("@(.+):(.+)").match(state_key)  # type: Match
                mxid = match.group(0)  # type: str
                displayname = content.get("displayname", None) or mxid  # type: str
                prev_displayname = prev_content.get("displayname", None) or mxid  # type: str
                if displayname != prev_displayname:
                    await self.handle_name_change(room_id, state_key, displayname,
                                                  prev_displayname, event_id)
            elif membership == "invite":
                await self.handle_invite(room_id, state_key, sender)
            elif prev_membership == "join" and membership == "leave":
                await self.handle_part(room_id, state_key, sender, event_id)
            elif membership == "join":
                await self.handle_join(room_id, state_key, event_id)
        elif evt_type in ("m.room.message", "m.sticker"):
            if evt_type != "m.room.message":
                content["msgtype"] = evt_type
            await self.handle_message(room_id, sender, content, event_id)
        elif evt_type == "m.room.redaction":
            await self.handle_redaction(room_id, sender, evt["redacts"])
        elif evt_type == "m.room.power_levels":
            prev_content = evt.get("unsigned", {}).get("prev_content", {})
            await self.handle_power_levels(room_id, sender, evt["content"], prev_content)
        elif evt_type in ("m.room.name", "m.room.avatar", "m.room.topic"):
            await self.handle_room_meta(evt_type, room_id, sender, evt["content"])
        elif evt_type == "m.room.pinned_events":
            new_events = set(evt["content"]["pinned"])
            try:
                old_events = set(evt["unsigned"]["prev_content"]["pinned"])
            except KeyError:
                old_events = set()
            await self.handle_room_pin(room_id, sender, new_events, old_events)
        elif evt_type == "m.room.tombstone":
            await self.handle_room_upgrade(room_id, evt["content"]["replacement_room"])
        elif evt_type == "m.receipt":
            await self.handle_read_receipts(room_id, self.parse_read_receipts(content))
        elif evt_type == "m.presence":
            await self.handle_presence(sender, content.get("presence", "offline"))
        elif evt_type == "m.typing":
            await self.handle_typing(room_id, content.get("user_ids", []))
        else:
            return
        if EVENT_TIME:
            EVENT_TIME.labels(event_type=evt_type).observe(time.time() - start_time)
