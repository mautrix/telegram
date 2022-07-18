# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2022 Tulir Asokan
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
from __future__ import annotations

from typing import TYPE_CHECKING

from mautrix.bridge import BaseMatrixHandler
from mautrix.errors import MatrixError
from mautrix.types import (
    Event,
    EventID,
    EventType,
    MemberStateEventContent,
    MessageType,
    PresenceEvent,
    PresenceState,
    ReactionEvent,
    ReceiptEvent,
    RedactionEvent,
    RoomAvatarStateEventContent as AvatarContent,
    RoomID,
    RoomNameStateEventContent as NameContent,
    RoomTopicStateEventContent as TopicContent,
    SingleReceiptEventContent,
    StateEvent,
    TextMessageEventContent,
    TypingEvent,
    UserID,
)

from . import commands as com, portal as po, puppet as pu, user as u
from .commands.portal.util import get_initial_state, user_has_power_level, warn_missing_power
from .types import TelegramID

if TYPE_CHECKING:
    from .__main__ import TelegramBridge


class MatrixHandler(BaseMatrixHandler):
    commands: com.CommandProcessor
    _previously_typing: dict[RoomID, set[UserID]]

    def __init__(self, bridge: "TelegramBridge") -> None:
        prefix, suffix = bridge.config["bridge.username_template"].format(userid=":").split(":")
        homeserver = bridge.config["homeserver.domain"]
        self.user_id_prefix = f"@{prefix}"
        self.user_id_suffix = f"{suffix}:{homeserver}"

        super().__init__(command_processor=com.CommandProcessor(bridge), bridge=bridge)

        self._previously_typing = {}

    async def handle_puppet_group_invite(
        self,
        room_id: RoomID,
        puppet: pu.Puppet,
        invited_by: u.User,
        evt: StateEvent,
        members: list[UserID],
    ) -> None:
        double_puppet = await pu.Puppet.get_by_custom_mxid(invited_by.mxid)
        if (
            not double_puppet
            or self.az.bot_mxid in members
            or not self.config["bridge.create_group_on_invite"]
        ):
            if self.az.bot_mxid not in members:
                await puppet.default_mxid_intent.leave_room(
                    room_id,
                    reason="This ghost does not join multi-user rooms without the bridge bot.",
                )
            else:
                await puppet.default_mxid_intent.send_notice(
                    room_id,
                    "This ghost will remain inactive "
                    "until a Telegram chat is created for this room.",
                )
            return
        elif not await user_has_power_level(
            evt.room_id, double_puppet.intent, invited_by, "bridge"
        ):
            await puppet.default_mxid_intent.leave_room(
                room_id, reason="You do not have the permissions to bridge this room."
            )
            return

        await double_puppet.intent.invite_user(room_id, self.az.bot_mxid)

        title, about, levels, encrypted = await get_initial_state(double_puppet.intent, room_id)
        if not title:
            await puppet.default_mxid_intent.leave_room(
                room_id, reason="Please set a title before inviting Telegram ghosts."
            )
            return

        portal = po.Portal(
            tgid=TelegramID(0),
            tg_receiver=TelegramID(0),
            peer_type="channel",
            mxid=evt.room_id,
            title=title,
            about=about,
            encrypted=encrypted,
        )
        await portal.az.intent.ensure_joined(room_id)
        levels = await portal.az.intent.get_power_levels(room_id)
        invited_by_level = levels.get_user_level(invited_by.mxid)
        if invited_by_level > levels.get_user_level(self.az.bot_mxid):
            levels.users[self.az.bot_mxid] = 100 if invited_by_level >= 100 else invited_by_level
            await double_puppet.intent.set_power_levels(room_id, levels)

        invites, errors = await portal.get_telegram_users_in_matrix_room(
            invited_by, pre_create=True
        )
        if len(errors) > 0:
            error_list = "\n".join(f"* [{mxid}](https://matrix.to/#/{mxid})" for mxid in errors)
            await portal.az.intent.send_notice(
                room_id,
                f"Failed to add the following users to the chat:\n\n{error_list}\n\n"
                "You can try `$cmdprefix+sp search -r <username>` to help the bridge find "
                "those users.",
            )

        try:
            await portal.create_telegram_chat(invited_by, invites=invites, supergroup=True)
        except ValueError as e:
            await portal.delete()
            await portal.az.intent.send_notice(room_id, e.args[0])
            return

    async def handle_invite(
        self, room_id: RoomID, user_id: UserID, inviter: u.User, event_id: EventID
    ) -> None:
        user = await u.User.get_by_mxid(user_id, create=False)
        if not user:
            return
        await user.ensure_started()
        portal = await po.Portal.get_by_mxid(room_id)
        if (
            user
            and portal
            and await user.has_full_access(allow_bot=True)
            and portal.allow_bridging
        ):
            await portal.handle_matrix_invite(inviter, user)

    async def handle_join(self, room_id: RoomID, user_id: UserID, event_id: EventID) -> None:
        user = await u.User.get_and_start_by_mxid(user_id)

        portal = await po.Portal.get_by_mxid(room_id)
        if not portal or not portal.allow_bridging:
            return

        if not user.relaybot_whitelisted:
            await portal.main_intent.kick_user(
                room_id, user.mxid, "You are not whitelisted on this Telegram bridge."
            )
            return
        elif not await user.is_logged_in() and not portal.has_bot:
            await portal.main_intent.kick_user(
                room_id,
                user.mxid,
                "This chat does not have a bot relaying messages for unauthenticated users.",
            )
            return

        self.log.debug(f"{user.mxid} joined {room_id}")
        if await user.is_logged_in() or portal.has_bot:
            await portal.join_matrix(user, event_id)

    async def handle_leave(self, room_id: RoomID, user_id: UserID, event_id: EventID) -> None:
        self.log.debug(f"{user_id} left {room_id}")
        portal = await po.Portal.get_by_mxid(room_id)
        if not portal or not portal.allow_bridging:
            return

        user = await u.User.get_by_mxid(user_id, create=False)
        if not user:
            return
        await user.ensure_started()
        await portal.leave_matrix(user, event_id)

    async def handle_kick_ban(
        self,
        ban: bool,
        room_id: RoomID,
        user_id: UserID,
        sender: UserID,
        reason: str,
        event_id: EventID,
    ) -> None:
        action = "banned" if ban else "kicked"
        self.log.debug(f"{user_id} was {action} from {room_id} by {sender} for {reason}")
        portal = await po.Portal.get_by_mxid(room_id)
        if not portal or not portal.allow_bridging:
            return

        if user_id == self.az.bot_mxid:
            # Direct chat portal unbridging is handled in portal.kick_matrix
            if portal.peer_type != "user":
                await portal.unbridge()
            return

        sender = await u.User.get_by_mxid(sender, create=False)
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

        user = await u.User.get_by_mxid(user_id, create=False)
        if not user:
            return
        await user.ensure_started()
        if ban:
            await portal.ban_matrix(user, sender)
        else:
            await portal.kick_matrix(user, sender)

    async def handle_kick(
        self, room_id: RoomID, user_id: UserID, kicked_by: UserID, reason: str, event_id: EventID
    ) -> None:
        await self.handle_kick_ban(False, room_id, user_id, kicked_by, reason, event_id)

    async def handle_unban(
        self, room_id: RoomID, user_id: UserID, unbanned_by: UserID, reason: str, event_id: EventID
    ) -> None:
        # TODO handle unbans properly instead of handling it as a kick
        await self.handle_kick_ban(False, room_id, user_id, unbanned_by, reason, event_id)

    async def handle_ban(
        self, room_id: RoomID, user_id: UserID, banned_by: UserID, reason: str, event_id: EventID
    ) -> None:
        await self.handle_kick_ban(True, room_id, user_id, banned_by, reason, event_id)

    async def allow_message(self, user: u.User) -> bool:
        return user.relaybot_whitelisted

    async def allow_command(self, user: u.User) -> bool:
        return user.whitelisted

    @staticmethod
    async def allow_bridging_message(user: u.User, portal: po.Portal) -> bool:
        return await user.is_logged_in() or portal.has_bot

    @staticmethod
    async def handle_redaction(evt: RedactionEvent) -> None:
        sender = await u.User.get_and_start_by_mxid(evt.sender)
        if not sender.relaybot_whitelisted:
            return

        portal = await po.Portal.get_by_mxid(evt.room_id)
        if not portal or not portal.allow_bridging:
            return

        await portal.handle_matrix_deletion(sender, evt.redacts, evt.event_id)

    @staticmethod
    async def handle_reaction(evt: ReactionEvent) -> None:
        sender = await u.User.get_and_start_by_mxid(evt.sender)
        if not await sender.has_full_access():
            return

        portal = await po.Portal.get_by_mxid(evt.room_id)
        if not portal or not portal.allow_bridging:
            return

        await portal.handle_matrix_reaction(
            sender, evt.content.relates_to.event_id, evt.content.relates_to.key, evt.event_id
        )

    @staticmethod
    async def handle_power_levels(evt: StateEvent) -> None:
        portal = await po.Portal.get_by_mxid(evt.room_id)
        sender = await u.User.get_and_start_by_mxid(evt.sender)
        if await sender.has_full_access(allow_bot=True) and portal and portal.allow_bridging:
            await portal.handle_matrix_power_levels(
                sender, evt.content.users, evt.unsigned.prev_content.users, evt.event_id
            )

    @staticmethod
    async def handle_room_meta(
        evt_type: EventType,
        room_id: RoomID,
        sender_mxid: UserID,
        content: NameContent | AvatarContent | TopicContent,
        event_id: EventID,
    ) -> None:
        portal = await po.Portal.get_by_mxid(room_id)
        sender = await u.User.get_and_start_by_mxid(sender_mxid)
        if await sender.has_full_access(allow_bot=True) and portal and portal.allow_bridging:
            handler, content_type, content_key = {
                EventType.ROOM_NAME: (portal.handle_matrix_title, NameContent, "name"),
                EventType.ROOM_TOPIC: (portal.handle_matrix_about, TopicContent, "topic"),
                EventType.ROOM_AVATAR: (portal.handle_matrix_avatar, AvatarContent, "url"),
            }[evt_type]
            if not isinstance(content, content_type):
                return
            await handler(sender, content[content_key], event_id)

    @staticmethod
    async def handle_room_pin(
        room_id: RoomID,
        sender_mxid: UserID,
        new_events: set[str],
        old_events: set[str],
        event_id: EventID,
    ) -> None:
        portal = await po.Portal.get_by_mxid(room_id)
        sender = await u.User.get_and_start_by_mxid(sender_mxid)
        if await sender.has_full_access(allow_bot=True) and portal and portal.allow_bridging:
            if not new_events:
                await portal.handle_matrix_unpin_all(sender, event_id)
            else:
                changes = {
                    event_id: event_id in new_events for event_id in new_events ^ old_events
                }
                await portal.handle_matrix_pin(sender, changes, event_id)

    @staticmethod
    async def handle_room_upgrade(
        room_id: RoomID, sender: UserID, new_room_id: RoomID, event_id: EventID
    ) -> None:
        portal = await po.Portal.get_by_mxid(room_id)
        if portal and portal.allow_bridging:
            await portal.handle_matrix_upgrade(sender, new_room_id, event_id)

    async def handle_member_info_change(
        self,
        room_id: RoomID,
        user_id: UserID,
        profile: MemberStateEventContent,
        prev_profile: MemberStateEventContent,
        event_id: EventID,
    ) -> None:
        if profile.displayname == prev_profile.displayname:
            return

        portal = await po.Portal.get_by_mxid(room_id)
        if not portal or not portal.has_bot or not portal.allow_bridging:
            return

        user = await u.User.get_and_start_by_mxid(user_id)
        if await user.needs_relaybot(portal):
            await portal.name_change_matrix(
                user, profile.displayname, prev_profile.displayname, event_id
            )

    async def handle_read_receipt(
        self, user: u.User, portal: po.Portal, event_id: EventID, data: SingleReceiptEventContent
    ) -> None:
        if not portal.allow_bridging:
            return
        await portal.mark_read(user, event_id, data.get("ts", 0))

    @staticmethod
    async def handle_presence(user_id: UserID, presence: PresenceState) -> None:
        user = await u.User.get_by_mxid(user_id, check_db=False, create=False)
        if user and await user.is_logged_in():
            await user.set_presence(presence == PresenceState.ONLINE)

    async def handle_typing(self, room_id: RoomID, now_typing: set[UserID]) -> None:
        portal = await po.Portal.get_by_mxid(room_id)
        if not portal or not portal.allow_bridging:
            return

        previously_typing = self._previously_typing.get(room_id, set())

        for user_id in set(previously_typing | now_typing):
            is_typing = user_id in now_typing
            was_typing = user_id in previously_typing
            if is_typing and was_typing:
                continue

            user = await u.User.get_by_mxid(user_id, check_db=False, create=False)
            if user and await user.is_logged_in():
                await portal.set_typing(user, is_typing)

        self._previously_typing[room_id] = now_typing

    async def handle_ephemeral_event(
        self, evt: ReceiptEvent | PresenceEvent | TypingEvent
    ) -> None:
        if evt.type == EventType.RECEIPT:
            await self.handle_receipt(evt)
        elif evt.type == EventType.PRESENCE:
            await self.handle_presence(evt.sender, evt.content.presence)
        elif evt.type == EventType.TYPING:
            await self.handle_typing(evt.room_id, set(evt.content.user_ids))

    async def handle_event(self, evt: Event) -> None:
        if evt.type == EventType.ROOM_REDACTION:
            await self.handle_redaction(evt)
        elif evt.type == EventType.REACTION:
            await self.handle_reaction(evt)

    async def handle_state_event(self, evt: StateEvent) -> None:
        if evt.type == EventType.ROOM_POWER_LEVELS:
            await self.handle_power_levels(evt)
        elif evt.type in (EventType.ROOM_NAME, EventType.ROOM_AVATAR, EventType.ROOM_TOPIC):
            await self.handle_room_meta(
                evt.type, evt.room_id, evt.sender, evt.content, evt.event_id
            )
        elif evt.type == EventType.ROOM_PINNED_EVENTS:
            new_events = set(evt.content.pinned)
            try:
                old_events = set(evt.unsigned.prev_content.pinned)
            except (KeyError, ValueError, TypeError, AttributeError):
                old_events = set()
            await self.handle_room_pin(
                evt.room_id, evt.sender, new_events, old_events, evt.event_id
            )
        elif evt.type == EventType.ROOM_TOMBSTONE:
            await self.handle_room_upgrade(
                evt.room_id, evt.sender, evt.content.replacement_room, evt.event_id
            )
