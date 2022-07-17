# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2021 Tulir Asokan
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

from telethon.tl.types import (
    ChannelParticipantAdmin,
    ChannelParticipantCreator,
    ChatBannedRights,
    ChatParticipantAdmin,
    ChatParticipantCreator,
    TypeChannelParticipant,
    TypeChat,
    TypeChatParticipant,
    TypeUser,
)

from mautrix.types import EventType, PowerLevelStateEventContent as PowerLevelContent, UserID

from .. import portal as po, puppet as pu, user as u
from ..types import TelegramID


def get_base_power_levels(
    portal: po.Portal, levels: PowerLevelContent = None, entity: TypeChat = None
) -> PowerLevelContent:
    levels = levels or PowerLevelContent()
    if portal.peer_type == "user":
        overrides = portal.config["bridge.initial_power_level_overrides.user"]
        levels.ban = overrides.get("ban", 100)
        levels.kick = overrides.get("kick", 100)
        levels.invite = overrides.get("invite", 100)
        levels.redact = overrides.get("redact", 0)
        levels.events[EventType.ROOM_NAME] = 0
        levels.events[EventType.ROOM_AVATAR] = 0
        levels.events[EventType.ROOM_TOPIC] = 0
        levels.state_default = overrides.get("state_default", 0)
        levels.users_default = overrides.get("users_default", 0)
        levels.events_default = overrides.get("events_default", 0)
    else:
        overrides = portal.config["bridge.initial_power_level_overrides.group"]
        dbr = entity.default_banned_rights
        if not dbr:
            portal.log.debug(f"default_banned_rights is None in {entity}")
            dbr = ChatBannedRights(
                invite_users=True,
                change_info=True,
                pin_messages=True,
                send_stickers=False,
                send_messages=False,
                until_date=None,
            )
        levels.ban = overrides.get("ban", 50)
        levels.kick = overrides.get("kick", 50)
        levels.redact = overrides.get("redact", 50)
        levels.invite = overrides.get("invite", 50 if dbr.invite_users else 0)
        levels.events[EventType.ROOM_ENCRYPTION] = 50 if portal.matrix.e2ee else 99
        levels.events[EventType.ROOM_TOMBSTONE] = 99
        levels.events[EventType.ROOM_NAME] = 50 if dbr.change_info else 0
        levels.events[EventType.ROOM_AVATAR] = 50 if dbr.change_info else 0
        levels.events[EventType.ROOM_TOPIC] = 50 if dbr.change_info else 0
        levels.events[EventType.ROOM_PINNED_EVENTS] = 50 if dbr.pin_messages else 0
        levels.events[EventType.ROOM_POWER_LEVELS] = 75
        levels.events[EventType.ROOM_HISTORY_VISIBILITY] = 75
        levels.events[EventType.STICKER] = 50 if dbr.send_stickers else levels.events_default
        levels.state_default = overrides.get("state_default", 50)
        levels.users_default = overrides.get("users_default", 0)
        levels.events_default = overrides.get(
            "events_default",
            50
            if portal.peer_type == "channel" and not entity.megagroup or dbr.send_messages
            else 0,
        )
    for evt_type, value in overrides.get("events", {}).items():
        levels.events[EventType.find(evt_type)] = value
    userlevel_overrides = overrides.get("users", {})
    bot_level = levels.get_user_level(portal.main_intent.mxid)
    for user, user_level in levels.users.items():
        if user_level < bot_level:
            levels.users[user] = userlevel_overrides.get(user, 0)
    if portal.main_intent.mxid not in levels.users:
        levels.users[portal.main_intent.mxid] = 100
    return levels


async def participants_to_power_levels(
    portal: po.Portal,
    users: list[TypeUser | TypeChatParticipant | TypeChannelParticipant],
    levels: PowerLevelContent,
) -> bool:
    bot_level = levels.get_user_level(portal.main_intent.mxid)
    if bot_level < levels.get_event_level(EventType.ROOM_POWER_LEVELS):
        return False
    changed = False
    admin_power_level = min(75 if portal.peer_type == "channel" else 50, bot_level)
    if levels.get_event_level(EventType.ROOM_POWER_LEVELS) != admin_power_level:
        changed = True
        levels.events[EventType.ROOM_POWER_LEVELS] = admin_power_level

    for user in users:
        # The User objects we get from TelegramClient.get_participants have a custom
        # participant property
        participant = getattr(user, "participant", user)

        puppet = await pu.Puppet.get_by_tgid(TelegramID(participant.user_id))
        user = await u.User.get_by_tgid(TelegramID(participant.user_id))
        new_level = _get_level_from_participant(portal.az.bot_mxid, participant, levels)

        if user:
            await user.register_portal(portal)
            changed = _participant_to_power_levels(levels, user, new_level, bot_level) or changed

        if puppet:
            changed = _participant_to_power_levels(levels, puppet, new_level, bot_level) or changed
    return changed


def _get_level_from_participant(
    bot_mxid: UserID,
    participant: TypeUser | TypeChatParticipant | TypeChannelParticipant,
    levels: PowerLevelContent,
) -> int:
    # TODO use the power level requirements to get better precision in channels
    if isinstance(participant, (ChatParticipantAdmin, ChannelParticipantAdmin)):
        return levels.state_default or 50
    elif isinstance(participant, (ChatParticipantCreator, ChannelParticipantCreator)):
        return levels.get_user_level(bot_mxid) - 5
    return levels.users_default or 0


def _participant_to_power_levels(
    levels: PowerLevelContent,
    user: u.User | pu.Puppet,
    new_level: int,
    bot_level: int,
) -> bool:
    new_level = min(new_level, bot_level)
    user_level = levels.get_user_level(user.mxid)
    if user_level != new_level and user_level < bot_level:
        levels.users[user.mxid] = new_level
        return True
    return False
