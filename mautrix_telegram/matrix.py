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
from typing import Dict, Set, Tuple, Union, Iterable, TYPE_CHECKING

from mautrix.bridge import BaseMatrixHandler
from mautrix.types import (Event, EventType, RoomID, UserID, EventID, ReceiptEvent, ReceiptType,
                           ReceiptEventContent, PresenceEvent, PresenceState, TypingEvent,
                           MessageEvent, StateEvent, RedactionEvent, RoomNameStateEventContent,
                           RoomAvatarStateEventContent, RoomTopicStateEventContent,
                           MemberStateEventContent, EncryptedEvent, TextMessageEventContent,
                           MessageType)
from mautrix.errors import MatrixError

from . import user as u, portal as po, puppet as pu, commands as com

if TYPE_CHECKING:
    from .context import Context
    from .bot import Bot

RoomMetaStateEventContent = Union[RoomNameStateEventContent, RoomAvatarStateEventContent,
                                  RoomTopicStateEventContent]


class MatrixHandler(BaseMatrixHandler):
    bot: 'Bot'
    commands: 'com.CommandProcessor'
    previously_typing: Dict[RoomID, Set[UserID]]

    def __init__(self, context: 'Context') -> None:
        prefix, suffix = context.config["bridge.username_template"].format(userid=":").split(":")
        homeserver = context.config["homeserver.domain"]
        self.user_id_prefix = f"@{prefix}"
        self.user_id_suffix = f"{suffix}:{homeserver}"

        super().__init__(command_processor=com.CommandProcessor(context), bridge=context.bridge)

        self.bot = context.bot
        self.previously_typing = {}

    async def handle_puppet_invite(self, room_id: RoomID, puppet: pu.Puppet, inviter: u.User,
                                   event_id: EventID) -> None:
        intent = puppet.default_mxid_intent
        self.log.debug(f"{inviter.mxid} invited puppet for {puppet.tgid} to {room_id}")
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
            members = await intent.get_room_members(room_id)
        except MatrixError:
            self.log.exception(f"Failed to get members after joining {room_id} as {intent.mxid}")
            return
        if self.az.bot_mxid not in members:
            if len(members) > 2:
                await intent.error_and_leave(room_id, text=None, html=(
                    f"Please invite "
                    f"<a href='https://matrix.to/#/{self.az.bot_mxid}'>the bridge bot</a> "
                    f"first if you want to create a Telegram chat."))
                return

            await intent.join_room(room_id)
            portal = po.Portal.get_by_tgid(puppet.tgid, inviter.tgid, "user")
            if portal.mxid:
                try:
                    await intent.invite_user(portal.mxid, inviter.mxid)
                    await intent.send_notice(
                        room_id, text=f"You already have a private chat with me: {portal.mxid}",
                        html=("You already have a private chat with me: "
                              f"<a href='https://matrix.to/#/{portal.mxid}'>Link to room</a>"))
                    await intent.leave_room(room_id)
                    return
                except MatrixError:
                    pass
            portal.mxid = room_id
            e2be_ok = None
            if self.config["bridge.encryption.default"] and self.e2ee:
                e2be_ok = await portal.enable_dm_encryption()
            await portal.save()
            await inviter.register_portal(portal)
            if e2be_ok is True:
                evt_type, content = await self.e2ee.encrypt(
                    room_id, EventType.ROOM_MESSAGE,
                    TextMessageEventContent(msgtype=MessageType.NOTICE,
                                            body="Portal to private chat created and end-to-bridge"
                                                 " encryption enabled."))
                await intent.send_message_event(room_id, evt_type, content)
            else:
                message = "Portal to private chat created."
                if e2be_ok is False:
                    message += "\n\nWarning: Failed to enable end-to-bridge encryption"
                await intent.send_notice(room_id, message)
        else:
            await intent.join_room(room_id)
            await intent.send_notice(room_id, "This puppet will remain inactive until a "
                                              "Telegram chat is created for this room.")

    async def send_welcome_message(self, room_id: RoomID, inviter: 'u.User') -> None:
        try:
            is_management = len(await self.az.intent.get_room_members(room_id)) == 2
        except MatrixError:
            # The AS bot is not in the room.
            return
        cmd_prefix = self.commands.command_prefix
        text = html = "Hello, I'm a Telegram bridge bot. "
        if is_management and inviter.puppet_whitelisted and not await inviter.is_logged_in():
            text += f"Use `{cmd_prefix} help` for help or `{cmd_prefix} login` to log in."
            html += (f"Use <code>{cmd_prefix} help</code> for help"
                     f" or <code>{cmd_prefix} login</code> to log in.")
        else:
            text += f"Use `{cmd_prefix} help` for help."
            html += f"Use <code>{cmd_prefix} help</code> for help."
        await self.az.intent.send_notice(room_id, text=text, html=html)

    async def handle_invite(self, room_id: RoomID, user_id: UserID, inviter: 'u.User',
                            event_id: EventID) -> None:
        user = u.User.get_by_mxid(user_id, create=False)
        if not user:
            return
        await user.ensure_started()
        portal = po.Portal.get_by_mxid(room_id)
        if user and await user.has_full_access(allow_bot=True) and portal:
            await portal.invite_telegram(inviter, user)

    async def handle_join(self, room_id: RoomID, user_id: UserID, event_id: EventID) -> None:
        user = await u.User.get_by_mxid(user_id).ensure_started()

        portal = po.Portal.get_by_mxid(room_id)
        if not portal:
            return

        if not user.relaybot_whitelisted:
            await portal.main_intent.kick_user(room_id, user.mxid,
                                               "You are not whitelisted on this Telegram bridge.")
            return
        elif not await user.is_logged_in() and not portal.has_bot:
            await portal.main_intent.kick_user(room_id, user.mxid,
                                               "This chat does not have a bot relaying "
                                               "messages for unauthenticated users.")
            return

        self.log.debug(f"{user.mxid} joined {room_id}")
        if await user.is_logged_in() or portal.has_bot:
            await portal.join_matrix(user, event_id)

    async def get_leave_handle_info(self) -> Tuple[po.Portal, u.User]:
        pass

    async def handle_leave(self, room_id: RoomID, user_id: UserID, event_id: EventID) -> None:
        self.log.debug(f"{user_id} left {room_id}")
        portal = po.Portal.get_by_mxid(room_id)
        if not portal:
            return

        user = u.User.get_by_mxid(user_id, create=False)
        if not user:
            return
        await user.ensure_started()
        await portal.leave_matrix(user, event_id)

    async def handle_kick_ban(self, ban: bool, room_id: RoomID, user_id: UserID, sender: UserID,
                              reason: str, event_id: EventID) -> None:
        action = "banned" if ban else "kicked"
        self.log.debug(f"{user_id} was {action} from {room_id} by {sender} for {reason}")
        portal = po.Portal.get_by_mxid(room_id)
        if not portal:
            return

        if user_id == self.az.bot_mxid:
            # Direct chat portal unbridging is handled in portal.kick_matrix
            if portal.peer_type != "user":
                await portal.unbridge()
            return

        sender = u.User.get_by_mxid(sender, create=False)
        if not sender:
            return
        await sender.ensure_started()

        puppet = await pu.Puppet.get_by_mxid(user_id)
        if puppet:
            if ban:
                await portal.ban_matrix(puppet, sender)
            else:
                await portal.kick_matrix(puppet, sender)
            return

        user = u.User.get_by_mxid(user_id, create=False)
        if not user:
            return
        await user.ensure_started()
        if ban:
            await portal.ban_matrix(user, sender)
        else:
            await portal.kick_matrix(user, sender)

    async def handle_kick(self, room_id: RoomID, user_id: UserID, kicked_by: UserID, reason: str,
                          event_id: EventID) -> None:
        await self.handle_kick_ban(False, room_id, user_id, kicked_by, reason, event_id)

    async def handle_unban(self, room_id: RoomID, user_id: UserID, unbanned_by: UserID,
                           reason: str, event_id: EventID) -> None:
        # TODO handle unbans properly instead of handling it as a kick
        await self.handle_kick_ban(False, room_id, user_id, unbanned_by, reason, event_id)

    async def handle_ban(self, room_id: RoomID, user_id: UserID, banned_by: UserID, reason: str,
                         event_id: EventID) -> None:
        await self.handle_kick_ban(True, room_id, user_id, banned_by, reason, event_id)

    @staticmethod
    async def allow_message(user: 'u.User') -> bool:
        return user.relaybot_whitelisted

    @staticmethod
    async def allow_command(user: 'u.User') -> bool:
        return user.whitelisted

    @staticmethod
    async def allow_bridging_message(user: 'u.User', portal: 'po.Portal') -> bool:
        return await user.is_logged_in() or portal.has_bot

    @staticmethod
    async def handle_redaction(evt: RedactionEvent) -> None:
        sender = await u.User.get_by_mxid(evt.sender).ensure_started()
        if not sender.relaybot_whitelisted:
            return

        portal = po.Portal.get_by_mxid(evt.room_id)
        if not portal:
            return

        await portal.handle_matrix_deletion(sender, evt.redacts, evt.event_id)

    @staticmethod
    async def handle_power_levels(evt: StateEvent) -> None:
        portal = po.Portal.get_by_mxid(evt.room_id)
        sender = await u.User.get_by_mxid(evt.sender).ensure_started()
        if await sender.has_full_access(allow_bot=True) and portal:
            await portal.handle_matrix_power_levels(sender, evt.content.users,
                                                    evt.unsigned.prev_content.users,
                                                    evt.event_id)

    @staticmethod
    async def handle_room_meta(evt_type: EventType, room_id: RoomID, sender_mxid: UserID,
                               content: RoomMetaStateEventContent, event_id: EventID) -> None:
        portal = po.Portal.get_by_mxid(room_id)
        sender = await u.User.get_by_mxid(sender_mxid).ensure_started()
        if await sender.has_full_access(allow_bot=True) and portal:
            handler, content_type, content_key = {
                EventType.ROOM_NAME: (portal.handle_matrix_title, RoomNameStateEventContent, "name"),
                EventType.ROOM_TOPIC: (portal.handle_matrix_about, RoomTopicStateEventContent, "topic"),
                EventType.ROOM_AVATAR: (portal.handle_matrix_avatar, RoomAvatarStateEventContent, "url"),
            }[evt_type]
            if not isinstance(content, content_type):
                return
            await handler(sender, content[content_key], event_id)

    @staticmethod
    async def handle_room_pin(room_id: RoomID, sender_mxid: UserID,
                              new_events: Set[str], old_events: Set[str],
                              event_id: EventID) -> None:
        portal = po.Portal.get_by_mxid(room_id)
        sender = await u.User.get_by_mxid(sender_mxid).ensure_started()
        if await sender.has_full_access(allow_bot=True) and portal:
            events = new_events - old_events
            if len(events) > 0:
                # New event pinned, set that as pinned in Telegram.
                await portal.handle_matrix_pin(sender, EventID(events.pop()), event_id)
            elif len(new_events) == 0:
                # All pinned events removed, remove pinned event in Telegram.
                await portal.handle_matrix_pin(sender, None, event_id)

    @staticmethod
    async def handle_room_upgrade(room_id: RoomID, sender: UserID, new_room_id: RoomID,
                                  event_id: EventID) -> None:
        portal = po.Portal.get_by_mxid(room_id)
        if portal:
            await portal.handle_matrix_upgrade(sender, new_room_id, event_id)

    async def handle_member_info_change(self, room_id: RoomID, user_id: UserID,
                                        profile: MemberStateEventContent,
                                        prev_profile: MemberStateEventContent,
                                        event_id: EventID) -> None:
        if profile.displayname == prev_profile.displayname:
            return

        portal = po.Portal.get_by_mxid(room_id)
        if not portal or not portal.has_bot:
            return

        user = await u.User.get_by_mxid(user_id).ensure_started()
        if await user.needs_relaybot(portal):
            await portal.name_change_matrix(user, profile.displayname, prev_profile.displayname,
                                            event_id)

    @staticmethod
    def parse_read_receipts(content: ReceiptEventContent) -> Iterable[Tuple[UserID, EventID]]:
        return ((user_id, event_id)
                for event_id, receipts in content.items()
                for user_id in receipts.get(ReceiptType.READ, {}))

    @staticmethod
    async def handle_read_receipts(room_id: RoomID, receipts: Iterable[Tuple[UserID, EventID]]
                                   ) -> None:
        portal = po.Portal.get_by_mxid(room_id)
        if not portal:
            return

        for user_id, event_id in receipts:
            user = u.User.get_by_mxid(user_id, check_db=False, create=False)
            if user and await user.is_logged_in():
                await portal.mark_read(user, event_id)

    @staticmethod
    async def handle_presence(user_id: UserID, presence: PresenceState) -> None:
        user = u.User.get_by_mxid(user_id, check_db=False, create=False)
        if user and await user.is_logged_in():
            await user.set_presence(presence == PresenceState.ONLINE)

    async def handle_typing(self, room_id: RoomID, now_typing: Set[UserID]) -> None:
        portal = po.Portal.get_by_mxid(room_id)
        if not portal:
            return

        previously_typing = self.previously_typing.get(room_id, set())

        for user_id in set(previously_typing | now_typing):
            is_typing = user_id in now_typing
            was_typing = user_id in previously_typing
            if is_typing and was_typing:
                continue

            user = u.User.get_by_mxid(user_id, check_db=False, create=False)
            if user and await user.is_logged_in():
                await portal.set_typing(user, is_typing)

        self.previously_typing[room_id] = now_typing

    def filter_matrix_event(self, evt: Event) -> bool:
        if isinstance(evt, (TypingEvent, ReceiptEvent, PresenceEvent)):
            return False
        elif not isinstance(evt, (RedactionEvent, MessageEvent, StateEvent, EncryptedEvent)):
            return True
        if evt.content.get(self.az.real_user_content_key, False):
            puppet = pu.Puppet.deprecated_sync_get_by_custom_mxid(evt.sender)
            if puppet:
                self.log.debug("Ignoring puppet-sent event %s", evt.event_id)
                return True
        return evt.sender and (evt.sender == self.az.bot_mxid
                               or pu.Puppet.get_id_from_mxid(evt.sender) is not None)

    async def handle_ephemeral_event(self, evt: Union[ReceiptEvent, PresenceEvent, TypingEvent]
                                     ) -> None:
        if evt.type == EventType.RECEIPT:
            await self.handle_read_receipts(evt.room_id, self.parse_read_receipts(evt.content))
        elif evt.type == EventType.PRESENCE:
            await self.handle_presence(evt.sender, evt.content.presence)
        elif evt.type == EventType.TYPING:
            await self.handle_typing(evt.room_id, set(evt.content.user_ids))

    async def handle_event(self, evt: Event) -> None:
        if evt.type == EventType.ROOM_REDACTION:
            await self.handle_redaction(evt)

    async def handle_state_event(self, evt: StateEvent) -> None:
        if evt.type == EventType.ROOM_POWER_LEVELS:
            await self.handle_power_levels(evt)
        elif evt.type in (EventType.ROOM_NAME, EventType.ROOM_AVATAR, EventType.ROOM_TOPIC):
            await self.handle_room_meta(evt.type, evt.room_id, evt.sender, evt.content,
                                        evt.event_id)
        elif evt.type == EventType.ROOM_PINNED_EVENTS:
            new_events = set(evt.content.pinned)
            try:
                old_events = set(evt.unsigned.prev_content.pinned)
            except (KeyError, ValueError, TypeError, AttributeError):
                old_events = set()
            await self.handle_room_pin(evt.room_id, evt.sender, new_events, old_events,
                                       evt.event_id)
        elif evt.type == EventType.ROOM_TOMBSTONE:
            await self.handle_room_upgrade(evt.room_id, evt.sender, evt.content.replacement_room,
                                           evt.event_id)
