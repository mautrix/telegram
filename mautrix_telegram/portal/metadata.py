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
from typing import List, Optional, Tuple, Union, Callable, TYPE_CHECKING
from abc import ABC
import asyncio

from telethon.tl.functions.messages import (AddChatUserRequest, CreateChatRequest,
                                            GetFullChatRequest, MigrateChatRequest)
from telethon.tl.functions.channels import (CreateChannelRequest, GetParticipantsRequest,
                                            InviteToChannelRequest, UpdateUsernameRequest)
from telethon.errors import ChatAdminRequiredError
from telethon.tl.types import (
    Channel, ChatBannedRights, ChannelParticipantsRecent, ChannelParticipantsSearch, ChatPhoto,
    PhotoEmpty, InputChannel, InputUser, ChatPhotoEmpty, PeerUser, Photo, TypeChat, TypeInputPeer,
    TypeUser, User, InputPeerPhotoFileLocation, ChatParticipantAdmin, ChannelParticipantAdmin,
    ChatParticipantCreator, ChannelParticipantCreator)

from mautrix.errors import MForbidden
from mautrix.types import (RoomID, UserID, RoomCreatePreset, EventType, Membership, Member,
                           PowerLevelStateEventContent, RoomAlias)
from mautrix.appservice import IntentAPI

from ..types import TelegramID
from ..context import Context
from .. import puppet as p, user as u, util
from .base import BasePortal, InviteList, TypeParticipant, TypeChatPhoto

if TYPE_CHECKING:
    from ..abstract_user import AbstractUser
    from ..config import Config

config: Optional['Config'] = None


class PortalMetadata(BasePortal, ABC):
    _room_create_lock: asyncio.Lock

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._room_create_lock = asyncio.Lock()

    # region Matrix -> Telegram

    async def _get_telegram_users_in_matrix_room(self) -> List[Union[InputUser, PeerUser]]:
        user_tgids = set()
        user_mxids = await self.main_intent.get_room_members(self.mxid, (Membership.JOIN,
                                                                         Membership.INVITE))
        for user_str in user_mxids:
            user = UserID(user_str)
            if user == self.az.bot_mxid:
                continue
            mx_user = u.User.get_by_mxid(user, create=False)
            if mx_user and mx_user.tgid:
                user_tgids.add(mx_user.tgid)
            puppet_id = p.Puppet.get_id_from_mxid(user)
            if puppet_id:
                user_tgids.add(puppet_id)
        return [PeerUser(user_id) for user_id in user_tgids]

    async def upgrade_telegram_chat(self, source: 'u.User') -> None:
        if self.peer_type != "chat":
            raise ValueError("Only normal group chats are upgradable to supergroups.")

        response = await source.client(MigrateChatRequest(chat_id=self.tgid))
        entity = None
        for chat in response.chats:
            if isinstance(chat, Channel):
                entity = chat
                break
        if not entity:
            raise ValueError("Upgrade may have failed: output channel not found.")
        self.peer_type = "channel"
        self._migrate_and_save_telegram(TelegramID(entity.id))
        await self.update_info(source, entity)

    def _migrate_and_save_telegram(self, new_id: TelegramID) -> None:
        try:
            del self.by_tgid[self.tgid_full]
        except KeyError:
            pass
        try:
            existing = self.by_tgid[(new_id, new_id)]
            existing.delete()
        except KeyError:
            pass
        self.db_instance.edit(tgid=new_id, tg_receiver=new_id, peer_type=self.peer_type)
        old_id = self.tgid
        self.tgid = new_id
        self.tg_receiver = new_id
        self.by_tgid[self.tgid_full] = self
        self.log = self.base_log.getChild(self.tgid_log)
        self.log.info(f"Telegram chat upgraded from {old_id}")

    async def set_telegram_username(self, source: 'u.User', username: str) -> None:
        if self.peer_type != "channel":
            raise ValueError("Only channels and supergroups have usernames.")
        await source.client(
            UpdateUsernameRequest(await self.get_input_entity(source), username))
        if await self._update_username(username):
            self.save()

    async def create_telegram_chat(self, source: 'u.User', supergroup: bool = False) -> None:
        if not self.mxid:
            raise ValueError("Can't create Telegram chat for portal without Matrix room.")
        elif self.tgid:
            raise ValueError("Can't create Telegram chat for portal with existing Telegram chat.")

        invites = await self._get_telegram_users_in_matrix_room()
        if len(invites) < 2:
            if self.bot is not None:
                info, mxid = await self.bot.get_me()
                raise ValueError("Not enough Telegram users to create a chat. "
                                 "Invite more Telegram ghost users to the room, such as the "
                                 f"relaybot ([{info.first_name}](https://matrix.to/#/{mxid})).")
            raise ValueError("Not enough Telegram users to create a chat. "
                             "Invite more Telegram ghost users to the room.")
        if self.peer_type == "chat":
            response = await source.client(CreateChatRequest(title=self.title, users=invites))
            entity = response.chats[0]
        elif self.peer_type == "channel":
            response = await source.client(CreateChannelRequest(title=self.title,
                                                                about=self.about or "",
                                                                megagroup=supergroup))
            entity = response.chats[0]
            await source.client(InviteToChannelRequest(
                channel=await source.client.get_input_entity(entity),
                users=invites))
        else:
            raise ValueError("Invalid peer type for Telegram chat creation")

        self.tgid = entity.id
        self.tg_receiver = self.tgid
        self.by_tgid[self.tgid_full] = self
        await self.update_info(source, entity)
        self.db_instance.insert()
        self.log = self.base_log.getChild(self.tgid_log)

        if self.bot and self.bot.tgid in invites:
            self.bot.add_chat(self.tgid, self.peer_type)

        levels = await self.main_intent.get_power_levels(self.mxid)
        if levels.get_user_level(self.main_intent.mxid) == 100:
            levels = self._get_base_power_levels(levels, entity)
            await self.main_intent.set_power_levels(self.mxid, levels)
        await self.handle_matrix_power_levels(source, levels.users, {})

    async def invite_telegram(self, source: 'u.User',
                              puppet: Union[p.Puppet, 'AbstractUser']) -> None:
        if self.peer_type == "chat":
            await source.client(
                AddChatUserRequest(chat_id=self.tgid, user_id=puppet.tgid, fwd_limit=0))
        elif self.peer_type == "channel":
            await source.client(InviteToChannelRequest(channel=self.peer, users=[puppet.tgid]))
        # We don't care if there are invites for private chat portals with the relaybot.
        elif not self.bot or self.tg_receiver != self.bot.tgid:
            raise ValueError("Invalid peer type for Telegram user invite")

    async def sync_matrix_members(self) -> None:
        resp = await self.main_intent.get_room_joined_memberships(self.mxid)
        members = resp["joined"]
        for mxid, info in members.items():
            member = Member(membership=Membership.JOIN)
            if "display_name" in info:
                member.displayname = info["display_name"]
            if "avatar_url" in info:
                member.avatar_url = info["avatar_url"]
            self.az.state_store.set_member(self.mxid, mxid, member)

    # endregion
    # region Telegram -> Matrix

    async def invite_to_matrix(self, users: InviteList) -> None:
        if isinstance(users, list):
            for user in users:
                await self.main_intent.invite_user(self.mxid, user, check_cache=True)
        else:
            await self.main_intent.invite_user(self.mxid, users, check_cache=True)

    async def update_matrix_room(self, user: 'AbstractUser', entity: Union[TypeChat, User],
                                 direct: bool = None, puppet: p.Puppet = None,
                                 levels: PowerLevelStateEventContent = None,
                                 users: List[User] = None,
                                 participants: List[TypeParticipant] = None) -> None:
        if direct is None:
            direct = self.peer_type == "user"
        try:
            await self._update_matrix_room(user, entity, direct, puppet, levels, users,
                                           participants)
        except Exception:
            self.log.exception("Fatal error updating Matrix room")

    async def _update_matrix_room(self, user: 'AbstractUser', entity: Union[TypeChat, User],
                                  direct: bool, puppet: p.Puppet = None,
                                  levels: PowerLevelStateEventContent = None,
                                  users: List[User] = None,
                                  participants: List[TypeParticipant] = None) -> None:
        if not direct:
            await self.update_info(user, entity)
            if not users or not participants:
                users, participants = await self._get_users(user, entity)
            await self._sync_telegram_users(user, users)
            await self.update_telegram_participants(participants, levels)
        else:
            if not puppet:
                puppet = p.Puppet.get(self.tgid)
            await puppet.update_info(user, entity)
            await puppet.intent_for(self).join_room(self.mxid)
        if self.sync_matrix_state:
            await self.sync_matrix_members()

    async def create_matrix_room(self, user: 'AbstractUser', entity: TypeChat = None,
                                 invites: InviteList = None, update_if_exists: bool = True,
                                 synchronous: bool = False) -> Optional[str]:
        if self.mxid:
            if update_if_exists:
                if not entity:
                    try:
                        entity = await self.get_entity(user)
                    except Exception:
                        self.log.exception(f"Failed to get entity through {user.tgid} for update")
                        return self.mxid
                update = self.update_matrix_room(user, entity, self.peer_type == "user")
                if synchronous:
                    await update
                else:
                    asyncio.ensure_future(update, loop=self.loop)
                await self.invite_to_matrix(invites or [])
            return self.mxid
        async with self._room_create_lock:
            try:
                return await self._create_matrix_room(user, entity, invites)
            except Exception:
                self.log.exception("Fatal error creating Matrix room")

    async def _create_matrix_room(self, user: 'AbstractUser', entity: TypeChat, invites: InviteList
                                  ) -> Optional[RoomID]:
        direct = self.peer_type == "user"

        if self.mxid:
            return self.mxid

        if not self.allow_bridging:
            return None

        if not entity:
            entity = await self.get_entity(user)
            self.log.debug(f"Fetched data: {entity}")

        self.log.debug("Creating room")

        try:
            self.title = entity.title
        except AttributeError:
            self.title = None

        if direct and self.tgid == user.tgid:
            self.title = "Telegram Saved Messages"
            self.about = "Your Telegram cloud storage chat"

        puppet = p.Puppet.get(self.tgid) if direct else None
        self._main_intent = puppet.intent_for(self) if direct else self.az.intent

        if self.peer_type == "channel":
            self.megagroup = entity.megagroup

        if self.peer_type == "channel" and entity.username:
            preset = RoomCreatePreset.PUBLIC
            self.username = entity.username
            alias = self.alias_localpart
        else:
            preset = RoomCreatePreset.PRIVATE
            # TODO invite link alias?
            alias = None

        if alias:
            # TODO? properly handle existing room aliases
            await self.main_intent.remove_room_alias(alias)

        power_levels = self._get_base_power_levels(entity=entity)
        users = participants = None
        if not direct:
            users, participants = await self._get_users(user, entity)
            if self.has_bot:
                extra_invites = config["bridge.relaybot.group_chat_invite"]
                invites += extra_invites
                for invite in extra_invites:
                    power_levels.users.setdefault(invite, 100)
            self._participants_to_power_levels(participants, power_levels)
        elif self.bot and self.tg_receiver == self.bot.tgid:
            invites = config["bridge.relaybot.private_chat.invite"]
            for invite in invites:
                power_levels.users.setdefault(invite, 100)
            self.title = puppet.displayname
        initial_state = [{
            "type": EventType.ROOM_POWER_LEVELS.serialize(),
            "content": power_levels.serialize(),
        }]
        if config["appservice.community_id"]:
            initial_state.append({
                "type": "m.room.related_groups",
                "content": {"groups": [config["appservice.community_id"]]},
            })
        creation_content = {}
        if not config["bridge.federate_rooms"]:
            creation_content["m.federate"] = False

        room_id = await self.main_intent.create_room(alias_localpart=alias, preset=preset,
                                                     is_direct=direct, invitees=invites or [],
                                                     name=self.title, topic=self.about,
                                                     initial_state=initial_state,
                                                     creation_content=creation_content)
        if not room_id:
            raise Exception(f"Failed to create room")

        self.mxid = RoomID(room_id)
        self.by_mxid[self.mxid] = self
        self.save()
        self.az.state_store.set_power_levels(self.mxid, power_levels)
        user.register_portal(self)
        asyncio.ensure_future(self.update_matrix_room(user, entity, direct, puppet,
                                                      levels=power_levels, users=users,
                                                      participants=participants), loop=self.loop)

        return self.mxid

    def _get_base_power_levels(self, levels: PowerLevelStateEventContent = None,
                               entity: TypeChat = None) -> PowerLevelStateEventContent:
        levels = levels or PowerLevelStateEventContent()
        if self.peer_type == "user":
            overrides = config["bridge.initial_power_level_overrides.user"]
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
            overrides = config["bridge.initial_power_level_overrides.group"]
            dbr = entity.default_banned_rights
            if not dbr:
                self.log.debug(f"default_banned_rights is None in {entity}")
                dbr = ChatBannedRights(invite_users=True, change_info=True, pin_messages=True,
                                       send_stickers=False, send_messages=False, until_date=None)
            levels.ban = overrides.get("ban", 50)
            levels.kick = overrides.get("kick", 50)
            levels.redact = overrides.get("redact", 50)
            levels.invite = overrides.get("invite", 50 if dbr.invite_users else 0)
            levels.events[EventType.ROOM_ENCRYPTED] = 99
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
            levels.events_default = (
                overrides.get("events_default",
                              50 if (self.peer_type == "channel" and not entity.megagroup
                                     or entity.default_banned_rights.send_messages)
                              else 0))
        for evt_type, value in overrides.get("events", {}).items():
            levels.events[EventType.find(evt_type)] = value
        levels.users = overrides.get("users", {})
        if self.main_intent.mxid not in levels.users:
            levels.users[self.main_intent.mxid] = 100
        return levels

    @staticmethod
    def _get_level_from_participant(participant: TypeParticipant) -> int:
        # TODO use the power level requirements to get better precision in channels
        if isinstance(participant, (ChatParticipantAdmin, ChannelParticipantAdmin)):
            return 50
        elif isinstance(participant, (ChatParticipantCreator, ChannelParticipantCreator)):
            return 95
        return 0

    @staticmethod
    def _participant_to_power_levels(levels: PowerLevelStateEventContent,
                                     user: Union['u.User', p.Puppet], new_level: int,
                                     bot_level: int) -> bool:
        new_level = min(new_level, bot_level)
        user_level = levels.get_user_level(user.mxid)
        if user_level != new_level and user_level < bot_level:
            levels.users[user.mxid] = new_level
            return True
        return False

    def _participants_to_power_levels(self, participants: List[TypeParticipant],
                                      levels: PowerLevelStateEventContent) -> bool:
        bot_level = levels.get_user_level(self.main_intent.mxid)
        if bot_level < levels.get_event_level(EventType.ROOM_POWER_LEVELS):
            return False
        changed = False
        admin_power_level = min(75 if self.peer_type == "channel" else 50, bot_level)
        if levels.events[EventType.ROOM_POWER_LEVELS] != admin_power_level:
            changed = True
            levels.events[EventType.ROOM_POWER_LEVELS] = admin_power_level

        for participant in participants:
            puppet = p.Puppet.get(TelegramID(participant.user_id))
            user = u.User.get_by_tgid(TelegramID(participant.user_id))
            new_level = self._get_level_from_participant(participant)

            if user:
                user.register_portal(self)
                changed = self._participant_to_power_levels(levels, user, new_level,
                                                            bot_level) or changed

            if puppet:
                changed = self._participant_to_power_levels(levels, puppet, new_level,
                                                            bot_level) or changed
        return changed

    async def update_telegram_participants(self, participants: List[TypeParticipant],
                                           levels: PowerLevelStateEventContent = None) -> None:
        if not levels:
            levels = await self.main_intent.get_power_levels(self.mxid)
        if self._participants_to_power_levels(participants, levels):
            await self.main_intent.set_power_levels(self.mxid, levels)

    def _add_bot_chat(self, bot: User) -> None:
        if self.bot and bot.id == self.bot.tgid:
            self.bot.add_chat(self.tgid, self.peer_type)
            return

        user = u.User.get_by_tgid(TelegramID(bot.id))
        if user and user.is_bot:
            user.register_portal(self)

    async def _sync_telegram_users(self, source: 'AbstractUser', users: List[User]) -> None:
        allowed_tgids = set()
        skip_deleted = config["bridge.skip_deleted_members"]
        for entity in users:
            if skip_deleted and entity.deleted:
                continue
            puppet = p.Puppet.get(TelegramID(entity.id))
            if entity.bot:
                self._add_bot_chat(entity)
            allowed_tgids.add(entity.id)
            await puppet.intent_for(self).ensure_joined(self.mxid)
            await puppet.update_info(source, entity)

            user = u.User.get_by_tgid(TelegramID(entity.id))
            if user:
                await self.invite_to_matrix(user.mxid)

        # We can't trust the member list if any of the following cases is true:
        #  * There are close to 10 000 users, because Telegram might not be sending all members.
        #  * The member sync count is limited, because then we might ignore some members.
        #  * It's a channel, because non-admins don't have access to the member list.
        trust_member_list = (len(allowed_tgids) < 9900
                             and self.max_initial_member_sync == -1
                             and (self.megagroup or self.peer_type != "channel"))
        if trust_member_list:
            joined_mxids = await self.main_intent.get_room_members(self.mxid)
            for user_mxid in joined_mxids:
                if user_mxid == self.az.bot_mxid:
                    continue
                puppet_id = p.Puppet.get_id_from_mxid(user_mxid)
                if puppet_id and puppet_id not in allowed_tgids:
                    if self.bot and puppet_id == self.bot.tgid:
                        self.bot.remove_chat(self.tgid)
                    try:
                        await self.main_intent.kick_user(self.mxid, user_mxid,
                                                         "User had left this Telegram chat.")
                    except MForbidden:
                        pass
                    continue
                mx_user = u.User.get_by_mxid(user_mxid, create=False)
                if mx_user and mx_user.is_bot and mx_user.tgid not in allowed_tgids:
                    mx_user.unregister_portal(self)

                if mx_user and not self.has_bot and mx_user.tgid not in allowed_tgids:
                    try:
                        await self.main_intent.kick_user(self.mxid, mx_user.mxid,
                                                         "You had left this Telegram chat.")
                    except MForbidden:
                        pass
                    continue

    async def _add_telegram_user(self, user_id: TelegramID, source: Optional['AbstractUser'] = None
                                 ) -> None:
        puppet = p.Puppet.get(user_id)
        if source:
            entity: User = await source.client.get_entity(PeerUser(user_id))
            await puppet.update_info(source, entity)
            await puppet.intent_for(self).ensure_joined(self.mxid)

        user = u.User.get_by_tgid(user_id)
        if user:
            user.register_portal(self)
            await self.invite_to_matrix(user.mxid)

    async def _delete_telegram_user(self, user_id: TelegramID, sender: p.Puppet) -> None:
        puppet = p.Puppet.get(user_id)
        user = u.User.get_by_tgid(user_id)
        kick_message = (f"Kicked by {sender.displayname}"
                        if sender and sender.tgid != puppet.tgid
                        else "Left Telegram chat")
        if sender.tgid != puppet.tgid:
            try:
                await sender.intent_for(self).kick_user(self.mxid, puppet.mxid)
            except MForbidden:
                await self.main_intent.kick_user(self.mxid, puppet.mxid, kick_message)
        else:
            await puppet.intent_for(self).leave_room(self.mxid)
        if user:
            user.unregister_portal(self)
            if sender.tgid != puppet.tgid:
                try:
                    await sender.intent_for(self).kick_user(self.mxid, puppet.mxid)
                    return
                except MForbidden:
                    pass
            try:
                await self.main_intent.kick_user(self.mxid, user.mxid, kick_message)
            except MForbidden as e:
                self.log.warning(f"Failed to kick {user.mxid}: {e}")

    async def update_info(self, user: 'AbstractUser', entity: TypeChat = None) -> None:
        if self.peer_type == "user":
            self.log.warning("Called update_info() for direct chat portal")
            return

        self.log.debug("Updating info")
        try:
            if not entity:
                entity = await self.get_entity(user)
                self.log.debug(f"Fetched data: {entity}")
            changed = False

            if self.peer_type == "channel":
                changed = self.megagroup != entity.megagroup or changed
                self.megagroup = entity.megagroup
                changed = await self._update_username(entity.username) or changed

            if hasattr(entity, "about"):
                changed = self._update_about(entity.about) or changed

            changed = await self._update_title(entity.title) or changed

            if isinstance(entity.photo, ChatPhoto):
                changed = await self._update_avatar(user, entity.photo) or changed
        except Exception:
            self.log.exception(f"Failed to update info from source {user.tgid}")

        if changed:
            self.save()

    async def _update_username(self, username: str, save: bool = False) -> bool:
        if self.username == username:
            return False

        if self.username:
            await self.main_intent.remove_room_alias(self.alias_localpart)
        self.username = username or None
        if self.username:
            await self.main_intent.add_room_alias(self.mxid, self.alias_localpart, override=True)
            if self.public_portals:
                await self.main_intent.set_join_rule(self.mxid, "public")
        else:
            await self.main_intent.set_join_rule(self.mxid, "invite")

        if save:
            self.save()
        return True

    async def _try_use_intent(self, sender: Optional['p.Puppet'],
                              action: Callable[[IntentAPI], None]) -> None:
        if sender:
            try:
                await action(sender.intent_for(self))
            except MForbidden:
                await action(self.main_intent)
        else:
            await action(self.main_intent)

    async def _update_about(self, about: str, sender: Optional['p.Puppet'] = None,
                            save: bool = False) -> bool:
        if self.about == about:
            return False

        self.about = about
        await self._try_use_intent(sender,
                                   lambda intent: intent.set_room_topic(self.mxid, self.about))
        if save:
            self.save()
        return True

    async def _update_title(self, title: str, sender: Optional['p.Puppet'] = None,
                            save: bool = False) -> bool:
        if self.title == title:
            return False

        self.title = title
        await self._try_use_intent(sender,
                                   lambda intent: intent.set_room_name(self.mxid, self.title))
        if save:
            self.save()
        return True

    async def _update_avatar(self, user: 'AbstractUser', photo: TypeChatPhoto,
                             sender: Optional['p.Puppet'] = None, save: bool = False) -> bool:
        if isinstance(photo, ChatPhoto):
            loc = InputPeerPhotoFileLocation(
                peer=await self.get_input_entity(user),
                local_id=photo.photo_big.local_id,
                volume_id=photo.photo_big.volume_id,
                big=True
            )
            photo_id = f"{loc.volume_id}-{loc.local_id}"
        elif isinstance(photo, Photo):
            loc, largest = self._get_largest_photo_size(photo)
            photo_id = f"{largest.location.volume_id}-{largest.location.local_id}"
        elif isinstance(photo, (ChatPhotoEmpty, PhotoEmpty)):
            photo_id = ""
            loc = None
        else:
            raise ValueError(f"Unknown photo type {type(photo)}")
        if self.photo_id != photo_id:
            if not photo_id:
                await self._try_use_intent(sender,
                                           lambda intent: intent.set_room_avatar(self.mxid, None))
                self.photo_id = ""
                if save:
                    self.save()
                return True
            file = await util.transfer_file_to_matrix(user.client, self.main_intent, loc)
            if file:
                await self._try_use_intent(sender, lambda intent: intent.set_room_avatar(self.mxid,
                                                                                         file.mxc))
                self.photo_id = photo_id
                if save:
                    self.save()
                return True
        return False

    async def _get_users(self, user: 'AbstractUser',
                         entity: Union[TypeInputPeer, InputUser, TypeChat, TypeUser, InputChannel]
                         ) -> Tuple[List[TypeUser], List[TypeParticipant]]:
        # TODO replace with client.get_participants
        if self.peer_type == "chat":
            chat = await user.client(GetFullChatRequest(chat_id=self.tgid))
            return chat.users, chat.full_chat.participants.participants
        elif self.peer_type == "channel":
            if not self.megagroup and not self.sync_channel_members:
                return [], []

            limit = self.max_initial_member_sync
            if limit == 0:
                return [], []

            try:
                if 0 < limit <= 200:
                    response = await user.client(GetParticipantsRequest(
                        entity, ChannelParticipantsRecent(), offset=0, limit=limit, hash=0))
                    return response.users, response.participants
                elif limit > 200 or limit == -1:
                    users: List[TypeUser] = []
                    participants: List[TypeParticipant] = []
                    offset = 0
                    remaining_quota = limit if limit > 0 else 1000000
                    query = (ChannelParticipantsSearch("") if limit == -1
                             else ChannelParticipantsRecent())
                    while True:
                        if remaining_quota <= 0:
                            break
                        response = await user.client(GetParticipantsRequest(
                            entity, query, offset=offset, limit=min(remaining_quota, 100), hash=0))
                        if not response.users:
                            break
                        participants += response.participants
                        users += response.users
                        offset += len(response.participants)
                        remaining_quota -= len(response.participants)
                    return users, participants
            except ChatAdminRequiredError:
                return [], []
        elif self.peer_type == "user":
            return [entity], []
        return [], []

    # endregion


def init(context: Context) -> None:
    global config
    config = context.config
