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
from collections import deque
from datetime import datetime
import asyncio
import random
import mimetypes
import unicodedata
import hashlib
import logging
import re

import magic
from sqlalchemy.exc import IntegrityError, InvalidRequestError
from sqlalchemy.orm.exc import FlushError

from telethon.tl.functions.messages import *
from telethon.tl.functions.channels import *
from telethon.errors.rpc_error_list import *
from telethon.tl.types import *
from mautrix_appservice import MatrixRequestError, IntentError

from .db import Portal as DBPortal, Message as DBMessage
from . import puppet as p, user as u, formatter, util

mimetypes.init()

config = None


class Portal:
    log = logging.getLogger("mau.portal")
    db = None
    az = None
    bot = None
    loop = None
    filter_mode = None
    filter_list = None
    bridge_notices = False
    alias_template = None
    mx_alias_regex = None
    hs_domain = None
    by_mxid = {}
    by_tgid = {}

    def __init__(self, tgid, peer_type, tg_receiver=None, mxid=None, username=None,
                 megagroup=False, title=None, about=None, photo_id=None, db_instance=None):
        self.mxid = mxid
        self.tgid = tgid
        self.tg_receiver = tg_receiver or tgid
        self.peer_type = peer_type
        self.username = username
        self.megagroup = megagroup
        self.title = title
        self.about = about
        self.photo_id = photo_id
        self._db_instance = db_instance

        self._main_intent = None
        self._room_create_lock = asyncio.Lock()
        self._temp_pinned_message_id = None
        self._temp_pinned_message_sender = None

        self._dedup = deque()
        self._dedup_mxid = {}
        self._dedup_action = deque()

        self._send_locks = {}

        if tgid:
            self.by_tgid[self.tgid_full] = self
        if mxid:
            self.by_mxid[mxid] = self

    # region Propegrties

    @property
    def tgid_full(self):
        return self.tgid, self.tg_receiver

    @property
    def tgid_log(self):
        if self.tgid == self.tg_receiver:
            return self.tgid
        return f"{self.tg_receiver}<->{self.tgid}"

    @property
    def peer(self):
        if self.peer_type == "user":
            return PeerUser(user_id=self.tgid)
        elif self.peer_type == "chat":
            return PeerChat(chat_id=self.tgid)
        elif self.peer_type == "channel":
            return PeerChannel(channel_id=self.tgid)

    @property
    def has_bot(self):
        return self.bot and self.bot.is_in_chat(self.tgid)

    @property
    def main_intent(self):
        if not self._main_intent:
            direct = self.peer_type == "user"
            puppet = p.Puppet.get(self.tgid) if direct else None
            self._main_intent = puppet.intent if direct else self.az.intent
        return self._main_intent

    # endregion
    # region Filtering

    def allow_bridging(self, tgid=None):
        tgid = tgid or self.tgid
        if self.peer_type == "user":
            return True
        elif self.filter_mode == "whitelist":
            return tgid in self.filter_list
        elif self.filter_mode == "blacklist":
            return tgid not in self.filter_list
        return True

    # endregion
    # region Deduplication

    @staticmethod
    def _hash_event(event):
        # Non-channel messages are unique per-user (wtf telegram), so we have no other choice than
        # to deduplicate based on a hash of the message content.

        # The timestamp is only accurate to the second, so we can't rely solely on that either.
        if isinstance(event, MessageService):
            hash_content = [event.date.timestamp(), event.from_id, event.action]
        else:
            hash_content = [event.date.timestamp(), event.message]
            if event.fwd_from:
                hash_content += [event.fwd_from.from_id, event.fwd_from.channel_id]
            elif isinstance(event, Message) and event.media:
                try:
                    hash_content += {
                        MessageMediaContact: lambda media: [media.user_id],
                        MessageMediaDocument: lambda media: [media.document.id],
                        MessageMediaPhoto: lambda media: [media.photo.id],
                        MessageMediaGeo: lambda media: [media.geo.long, media.geo.lat],
                    }[type(event.media)](event.media)
                except KeyError:
                    pass
        return hashlib.md5("-"
                           .join(str(a) for a in hash_content)
                           .encode("utf-8")
                           ).hexdigest()

    def is_duplicate_action(self, event):
        hash = self._hash_event(event) if self.peer_type != "channel" else event.id
        if hash in self._dedup_action:
            return True

        self._dedup_action.append(hash)

        if len(self._dedup_action) > 20:
            self._dedup_action.popleft()
        return False

    def update_duplicate(self, event, mxid=None, expected_mxid=None, force_hash=False):
        hash = self._hash_event(event) if self.peer_type != "channel" or force_hash else event.id
        try:
            found_mxid = self._dedup_mxid[hash]
        except KeyError:
            return 0, "None"

        if found_mxid != expected_mxid:
            return found_mxid
        self._dedup_mxid[hash] = mxid
        return None

    def is_duplicate(self, event, mxid=None, force_hash=False):
        hash = self._hash_event(event) if self.peer_type != "channel" or force_hash else event.id
        if hash in self._dedup:
            return self._dedup_mxid[hash]

        self._dedup_mxid[hash] = mxid
        self._dedup.append(hash)

        if len(self._dedup) > 20:
            del self._dedup_mxid[self._dedup.popleft()]
        return None

    def get_input_entity(self, user):
        return user.client.get_input_entity(self.peer)

    # endregion
    # region Matrix room info updating

    async def invite_to_matrix(self, users):
        if isinstance(users, str):
            await self.main_intent.invite(self.mxid, users, check_cache=True)
        elif isinstance(users, list):
            for user in users:
                await self.main_intent.invite(self.mxid, user, check_cache=True)
        else:
            raise ValueError("Invalid invite identifier given to invite_matrix()")

    async def update_matrix_room(self, user, entity, direct, puppet=None,
                                 levels=None, users=None, participants=None):
        if not direct:
            await self.update_info(user, entity)
            if not users or not participants:
                users, participants = await self._get_users(user, entity)
            await self.sync_telegram_users(user, users)
            await self.update_telegram_participants(participants, levels)
        else:
            if not puppet:
                puppet = p.Puppet.get(self.tgid)
            await puppet.update_info(user, entity)
            await puppet.intent.join_room(self.mxid)

    async def create_matrix_room(self, user, entity=None, invites=None, update_if_exists=True,
                                 synchronous=False):
        if self.mxid:
            if update_if_exists:
                if not entity:
                    entity = await user.client.get_entity(self.peer)
                update = self.update_matrix_room(user, entity, self.peer_type == "user")
                if synchronous:
                    await update
                else:
                    asyncio.ensure_future(update, loop=self.loop)
                await self.invite_to_matrix(invites or [])
            return self.mxid
        async with self._room_create_lock:
            return await self._create_matrix_room(user, entity, invites)

    async def _create_matrix_room(self, user, entity, invites):
        direct = self.peer_type == "user"

        if self.mxid:
            return self.mxid

        if not self.allow_bridging():
            return None

        if not entity:
            entity = await user.client.get_entity(self.peer)
            self.log.debug("Fetched data: %s", entity)

        self.log.debug(f"Creating room for {self.tgid_log}")

        try:
            self.title = entity.title
        except AttributeError:
            self.title = None

        puppet = p.Puppet.get(self.tgid) if direct else None
        self._main_intent = puppet.intent if direct else self.az.intent

        if self.peer_type == "channel":
            self.megagroup = entity.megagroup

        if self.peer_type == "channel" and entity.username:
            public = config["bridge.public_portals"]
            alias = self._get_alias_localpart(entity.username)
            self.username = entity.username
        else:
            public = False
            # TODO invite link alias?
            alias = None

        if alias:
            # TODO? properly handle existing room aliases
            await self.main_intent.remove_room_alias(alias)

        power_levels = self._get_base_power_levels({}, entity)
        users = participants = None
        if not direct:
            users, participants = await self._get_users(user, entity)
            self._participants_to_power_levels(participants, power_levels)
        initial_state = [{
            "type": "m.room.power_levels",
            "content": power_levels,
        }]

        room = await self.main_intent.create_room(alias=alias, is_public=public, is_direct=direct,
                                                  invitees=invites or [], name=self.title,
                                                  initial_state=initial_state)
        if not room:
            raise Exception(f"Failed to create room for {self.tgid_log}")

        self.mxid = room["room_id"]
        self.by_mxid[self.mxid] = self
        self.save()
        self.az.state_store.set_power_levels(self.mxid, power_levels)
        user.register_portal(self)
        asyncio.ensure_future(self.update_matrix_room(user, entity, direct, puppet,
                                                      levels=power_levels, users=users,
                                                      participants=participants),
                              loop=self.loop)

    def _get_base_power_levels(self, levels=None, entity=None):
        levels = levels or {}
        power_level_requirement = (0 if self.peer_type == "chat" and not entity.admins_enabled
                                   else 50)
        levels["ban"] = 99
        levels["invite"] = power_level_requirement if self.peer_type == "chat" else 75
        if "events" not in levels:
            levels["events"] = {}
        levels["events"]["m.room.name"] = power_level_requirement
        levels["events"]["m.room.avatar"] = power_level_requirement
        levels["events"]["m.room.topic"] = 50 if self.peer_type == "channel" else 99
        levels["events"]["m.room.power_levels"] = 75
        levels["events"]["m.room.history_visibility"] = 75
        levels["state_default"] = 50
        levels["users_default"] = 0
        levels["events_default"] = (50 if self.peer_type == "channel" and not entity.megagroup
                                    else 0)
        if "users" not in levels:
            levels["users"] = {
                self.main_intent.mxid: 100
            }
        else:
            levels["users"][self.main_intent.mxid] = 100
        return levels

    @property
    def alias(self):
        if not self.username:
            return None
        return f"#{self._get_alias_localpart()}:{self.hs_domain}"

    def _get_alias_localpart(self, username=None):
        username = username or self.username
        if not username:
            return None
        return self.alias_template.format(groupname=username)

    async def sync_telegram_users(self, source, users):
        allowed_tgids = set()
        for entity in users:
            puppet = p.Puppet.get(entity.id)
            if self.bot and puppet.tgid == self.bot.tgid:
                self.bot.add_chat(self.tgid, self.peer_type)
            allowed_tgids.add(entity.id)
            await puppet.intent.ensure_joined(self.mxid)
            await puppet.update_info(source, entity)

            user = u.User.get_by_tgid(entity.id)
            if user:
                await self.invite_to_matrix(user.mxid)

        # We can't trust the member list if any of the following cases is true:
        #  * There are close to 10 000 users, because Telegram might not be sending all members.
        #  * The member sync count is limited, because then we might ignore some members.
        #  * It's a channel, because non-admins don't have access to the member list.
        trust_member_list = (len(allowed_tgids) < 9900
                             and config["bridge.max_initial_member_sync"] == -1
                             and (self.megagroup or self.peer_type != "channel"))
        if trust_member_list:
            joined_mxids = await self.main_intent.get_room_members(self.mxid)
            for user in joined_mxids:
                if user == self.az.bot_mxid:
                    continue
                puppet_id = p.Puppet.get_id_from_mxid(user)
                if puppet_id and puppet_id not in allowed_tgids:
                    if self.bot and puppet_id == self.bot.tgid:
                        self.bot.remove_chat(self.tgid)
                    await self.main_intent.kick(self.mxid, user,
                                                "User had left this Telegram chat.")
                    continue
                mx_user = u.User.get_by_mxid(user, create=False)
                if mx_user and not self.has_bot and mx_user.tgid not in allowed_tgids:
                    await self.main_intent.kick(self.mxid, mx_user.mxid,
                                                "You had left this Telegram chat.")
                    continue

    async def add_telegram_user(self, user_id, source=None):
        puppet = p.Puppet.get(user_id)
        if source:
            entity = await source.client.get_entity(PeerUser(user_id))
            await puppet.update_info(source, entity)
            await puppet.intent.join_room(self.mxid)

        user = u.User.get_by_tgid(user_id)
        if user:
            user.register_portal(self)
            await self.invite_to_matrix(user.mxid)

    async def delete_telegram_user(self, user_id, sender):
        puppet = p.Puppet.get(user_id)
        user = u.User.get_by_tgid(user_id)
        kick_message = (f"Kicked by {sender.displayname}"
                        if sender and sender.tgid != puppet.tgid
                        else "Left Telegram chat")
        if sender and sender.tgid != puppet.tgid:
            await self.main_intent.kick(self.mxid, puppet.mxid, kick_message)
        else:
            await puppet.intent.leave_room(self.mxid)
        if user:
            user.unregister_portal(self)
            await self.main_intent.kick(self.mxid, user.mxid, kick_message)

    async def update_info(self, user, entity=None):
        if self.peer_type == "user":
            self.log.warning(f"Called update_info() for direct chat portal {self.tgid_log}")
            return

        self.log.debug(f"Updating info of {self.tgid_log}")
        if not entity:
            entity = await user.client.get_entity(self.peer)
            self.log.debug("Fetched data: %s", entity)
        changed = False

        if self.peer_type == "channel":
            changed = await self.update_username(entity.username) or changed
            # TODO update about text
            # changed = self.update_about(entity.about) or changed

        changed = await self.update_title(entity.title) or changed

        if isinstance(entity.photo, ChatPhoto):
            changed = await self.update_avatar(user, entity.photo.photo_big) or changed

        if changed:
            self.save()

    async def update_username(self, username, save=False):
        if self.username != username:
            if self.username:
                await self.main_intent.remove_room_alias(self._get_alias_localpart())
            self.username = username or None
            if self.username:
                await self.main_intent.add_room_alias(self.mxid, self._get_alias_localpart())
                if config["bridge.public_portals"]:
                    await self.main_intent.set_join_rule(self.mxid, "public")
            else:
                await self.main_intent.set_join_rule(self.mxid, "invite")

            if save:
                self.save()
            return True
        return False

    async def update_about(self, about, save=False):
        if self.about != about:
            self.about = about
            await self.main_intent.set_room_topic(self.mxid, self.about)
            if save:
                self.save()
            return True
        return False

    async def update_title(self, title, save=False):
        if self.title != title:
            self.title = title
            await self.main_intent.set_room_name(self.mxid, self.title)
            if save:
                self.save()
            return True
        return False

    @staticmethod
    def _get_largest_photo_size(photo):
        return max(photo.sizes, key=(lambda photo2: (
            len(photo2.bytes) if isinstance(photo2, PhotoCachedSize) else photo2.size)))

    async def remove_avatar(self, user, save=False):
        await self.main_intent.set_room_avatar(self.mxid, None)
        self.photo_id = None
        if save:
            self.save()

    async def update_avatar(self, user, photo, save=False):
        photo_id = f"{photo.volume_id}-{photo.local_id}"
        if self.photo_id != photo_id:
            file = await util.transfer_file_to_matrix(self.db, user.client, self.main_intent,
                                                      photo)
            if file:
                await self.main_intent.set_room_avatar(self.mxid, file.mxc)
                self.photo_id = photo_id
                if save:
                    self.save()
                return True
        return False

    async def _get_users(self, user, entity):
        if self.peer_type == "chat":
            chat = await user.client(GetFullChatRequest(chat_id=self.tgid))
            return chat.users, chat.full_chat.participants.participants
        elif self.peer_type == "channel":
            limit = config["bridge.max_initial_member_sync"]
            if limit == 0:
                return [], []

            try:
                if 0 < limit <= 200:
                    response = await user.client(GetParticipantsRequest(
                        entity, ChannelParticipantsRecent(), offset=0, limit=limit, hash=0))
                    return response.users, response.participants
                elif limit > 200 or limit == -1:
                    users, participants = [], []
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

    async def get_invite_link(self, user):
        if self.peer_type == "user":
            raise ValueError("You can't invite users to private chats.")
        elif self.peer_type == "chat":
            link = await user.client(ExportChatInviteRequest(chat_id=self.tgid))
        elif self.peer_type == "channel":
            if self.username:
                return f"https://t.me/{self.username}"
            link = await user.client(
                ExportInviteRequest(channel=await self.get_input_entity(user)))
        else:
            raise ValueError(f"Invalid peer type '{self.peer_type}' for invite link.")

        if isinstance(link, ChatInviteEmpty):
            raise ValueError("Failed to get invite link.")

        return link.link

    async def get_authenticated_matrix_users(self):
        try:
            members = await self.main_intent.get_room_members(self.mxid)
        except MatrixRequestError:
            return []
        authenticated = []
        has_bot = self.has_bot
        for member in members:
            if p.Puppet.get_id_from_mxid(member) or member == self.main_intent.mxid:
                continue
            user = await u.User.get_by_mxid(member).ensure_started()
            if (has_bot and user.relaybot_whitelisted) or user.has_full_access:
                authenticated.append(user)
        return authenticated

    @staticmethod
    async def cleanup_room(intent, room_id, message="Portal deleted", puppets_only=False):
        try:
            members = await intent.get_room_members(room_id)
        except MatrixRequestError:
            members = []
        for user in members:
            puppet = p.Puppet.get_by_mxid(user, create=False)
            if user != intent.mxid and (not puppets_only or puppet):
                try:
                    if puppet:
                        await puppet.intent.leave_room(room_id)
                    else:
                        await intent.kick(room_id, user, message)
                except (MatrixRequestError, IntentError):
                    pass
        await intent.leave_room(room_id)

    async def unbridge(self):
        await self.cleanup_room(self.main_intent, self.mxid, "Room unbridged", puppets_only=True)
        self.delete()

    async def cleanup_and_delete(self):
        await self.cleanup_room(self.main_intent, self.mxid)
        self.delete()

    # endregion
    # region Matrix event handling

    @staticmethod
    def _get_file_meta(body, mime):
        try:
            current_extension = body[body.rindex("."):]
            if mimetypes.types_map[current_extension] == mime:
                return body
        except (ValueError, KeyError):
            pass
        if mime:
            return f"matrix_upload{mimetypes.guess_extension(mime)}"
        else:
            return ""

    async def leave_matrix(self, user, source, event_id):
        if not user.logged_in:
            async with self.require_send_lock(self.bot.tgid):
                response = await self.bot.client.send_message(
                    self.peer, f"__{user.displayname} left the room.__", markdown=True)
                space = self.tgid if self.peer_type == "channel" else self.bot.tgid
                self.is_duplicate(response, (event_id, space))
            return

        if self.peer_type == "user":
            await self.main_intent.leave_room(self.mxid)
            self.delete()
            try:
                del self.by_tgid[self.tgid_full]
                del self.by_mxid[self.mxid]
            except KeyError:
                pass
        elif source and source.tgid != user.tgid:
            if self.peer_type == "chat":
                await source.client(DeleteChatUserRequest(chat_id=self.tgid, user_id=user.tgid))
            else:
                channel = await self.get_input_entity(source)
                rights = ChannelBannedRights(datetime.fromtimestamp(0), True)
                await source.client(EditBannedRequest(channel=channel,
                                                      user_id=user.tgid,
                                                      banned_rights=rights))
        elif self.peer_type == "chat":
            await user.client(DeleteChatUserRequest(chat_id=self.tgid, user_id=InputUserSelf()))
        elif self.peer_type == "channel":
            channel = await self.get_input_entity(user)
            await user.client(LeaveChannelRequest(channel=channel))

    async def join_matrix(self, user, event_id):
        if not user.logged_in:
            async with self.require_send_lock(self.bot.tgid):
                response = await self.bot.client.send_message(
                    self.peer, f"__{user.displayname} joined the room.__", markdown=True)
                space = self.tgid if self.peer_type == "channel" else self.bot.tgid
                self.is_duplicate(response, (event_id, space))
            return

        if self.peer_type == "channel":
            await user.client(JoinChannelRequest(channel=await self.get_input_entity(user)))
        else:
            # We'll just assume the user is already in the chat.
            pass

    @staticmethod
    def _preprocess_matrix_message(sender, message):
        msgtype = message["msgtype"]
        if msgtype == "m.emote":
            if "formatted_body" in message:
                message["formatted_body"] = f"* {sender.displayname} {message['formatted_body']}"
            message["body"] = f"* {sender.displayname} {message['body']}"
            message["msgtype"] = "m.text"
        elif not sender.logged_in:
            html = message["formatted_body"] if "formatted_body" in message else None
            text = message["body"]
            if msgtype == "m.text":
                if html:
                    html = f"&lt;{sender.displayname}&gt; {html}"
                text = f"<{sender.displayname}> {text}"
            else:
                msgtype = msgtype[len("m."):]
                prefix = {
                    "file": "a ",
                    "image": "an ",
                    "audio": "",
                    "video": "a ",
                    "location": "a ",
                }.get(msgtype, "")
                if html:
                    html = f"{sender.displayname} sent {prefix}{msgtype}: {html}"
                text = ": " + text if text else ""
                text = f"{sender.displayname} sent {prefix}{msgtype}{text}"
            if html:
                message["formatted_body"] = html
            message["body"] = text

    async def _matrix_event_to_entities(self, client, event):
        try:
            if event.get("format", None) == "org.matrix.custom.html":
                message, entities = formatter.matrix_to_telegram(event["formatted_body"])

                # TODO remove this crap
                for entity in entities:
                    if isinstance(entity, InputMessageEntityMentionName):
                        entity.user_id = await client.get_input_entity(
                            PeerUser(entity.user_id.user_id))
            else:
                message, entities = formatter.matrix_text_to_telegram(event["body"])
        except KeyError:
            message, entities = None, None
        return message, entities

    def require_send_lock(self, id):
        if id is None:
            return None
        try:
            return self._send_locks[id]
        except KeyError:
            self._send_locks[id] = asyncio.Lock()
            return self._send_locks[id]

    def optional_send_lock(self, id):
        if id is None:
            return None
        try:
            return self._send_locks[id]
        except KeyError:
            return None

    async def _handle_matrix_text(self, sender_id, event_id, space, client, message, reply_to):
        message, entities = await self._matrix_event_to_entities(client, message)

        if len(message) > 4096:
            message = message[0:4082] + " [message cut]"
            new_entities = []
            for entity in entities:
                if entity.offset > 4082:
                    continue
                if entity.offset + entity.length > 4082:
                    entity.length = 4082 - entity.offset
                new_entities.append(entity)
            new_entities.append(MessageEntityItalic(4082, len(" [message cut]")))
            entities = new_entities

        lock = self.require_send_lock(sender_id)
        async with lock:
            response = await client.send_message(self.peer, message, entities=entities,
                                                 reply_to=reply_to)
            self._add_telegram_message_to_db(event_id, space, response)

    async def _handle_matrix_file(self, type, sender_id, event_id, space, client, message,
                                  reply_to):
        file = await self.main_intent.download_file(message["url"])

        info = message.get("info", {})
        mime = info.get("mimetype", None)

        w, h = None, None

        if type == "m.sticker":
            if mime != "image/gif":
                mime, file, w, h = util.convert_image(file, source_mime=mime, target_type="webp")
            else:
                # Remove sticker description
                message["mxtg_filename"] = "sticker.gif"
                message["body"] = ""
        elif "w" in info and "h" in info:
            w, h = info["w"], info["h"]

        file_name = self._get_file_meta(message["mxtg_filename"], mime)

        attributes = [DocumentAttributeFilename(file_name=file_name)]
        if w and h:
            attributes.append(DocumentAttributeImageSize(w, h))

        caption = message["body"] if message["body"] != file_name else None

        media = await client.upload_file(file, mime, attributes, file_name)
        lock = self.require_send_lock(sender_id)
        async with lock:
            response = await client.send_media(self.peer, media, reply_to=reply_to,
                                               caption=caption)
            self._add_telegram_message_to_db(event_id, space, response)

    async def _handle_matrix_location(self, sender_id, event_id, space, client, message, reply_to):
        try:
            lat, long = message["geo_uri"][len("geo:"):].split(",")
            lat, long = float(lat), float(long)
        except (KeyError, ValueError):
            self.log.exception("Failed to parse location")
            return None
        message, entities = await self._matrix_event_to_entities(client, message)
        media = MessageMediaGeo(geo=GeoPoint(lat, long))

        lock = self.require_send_lock(sender_id)
        async with lock:
            response = await client.send_media(self.peer, media, reply_to=reply_to,
                                               caption=message, entities=entities)
            self._add_telegram_message_to_db(event_id, space, response)

    def _add_telegram_message_to_db(self, event_id, space, response):
        self.log.debug("Handled Matrix message: %s", response)
        self.is_duplicate(response, (event_id, space))
        self.db.add(DBMessage(
            tgid=response.id,
            tg_space=space,
            mx_room=self.mxid,
            mxid=event_id))
        self.db.commit()

    async def handle_matrix_message(self, sender, message, event_id):
        client = sender.client if sender.logged_in else self.bot.client
        sender_id = sender.tgid if sender.logged_in else self.bot.tgid
        space = (self.tgid if self.peer_type == "channel"  # Channels have their own ID space
                 else (sender.tgid if sender.logged_in else self.bot.tgid))
        reply_to = formatter.matrix_reply_to_telegram(message, space, room_id=self.mxid)

        message["mxtg_filename"] = message["body"]
        self._preprocess_matrix_message(sender, message)
        type = message["msgtype"]

        if type == "m.text" or (self.bridge_notices and type == "m.notice"):
            await self._handle_matrix_text(sender_id, event_id, space, client, message, reply_to)
        elif type == "m.location":
            await self._handle_matrix_location(sender_id, event_id, space, client, message,
                                               reply_to)
        elif type in ("m.sticker", "m.image", "m.file", "m.audio", "m.video"):
            await self._handle_matrix_file(type, sender_id, event_id, space, client, message,
                                           reply_to)
        else:
            self.log.debug("Unhandled Matrix event: %s", message)

    async def handle_matrix_pin(self, sender, pinned_message):
        if self.peer_type != "channel":
            return
        try:
            if not pinned_message:
                await sender.client(UpdatePinnedMessageRequest(channel=self.peer, id=0))
            else:
                message = DBMessage.query.filter(DBMessage.mxid == pinned_message,
                                                 DBMessage.tg_space == self.tgid,
                                                 DBMessage.mx_room == self.mxid).one_or_none()
                await sender.client(UpdatePinnedMessageRequest(channel=self.peer, id=message.tgid))
        except ChatNotModifiedError:
            pass

    async def handle_matrix_deletion(self, deleter, event_id):
        deleter = deleter if deleter.logged_in else self.bot
        space = self.tgid if self.peer_type == "channel" else deleter.tgid
        message = DBMessage.query.filter(DBMessage.mxid == event_id,
                                         DBMessage.tg_space == space,
                                         DBMessage.mx_room == self.mxid).one_or_none()
        if not message:
            return
        await deleter.client.delete_messages(self.peer, [message.tgid])

    async def _update_telegram_power_level(self, sender, user_id, level):
        if self.peer_type == "chat":
            await sender.client(EditChatAdminRequest(
                chat_id=self.tgid, user_id=user_id, is_admin=level >= 50))
        elif self.peer_type == "channel":
            moderator = level >= 50
            admin = level >= 75
            rights = ChannelAdminRights(change_info=moderator, post_messages=moderator,
                                        edit_messages=moderator, delete_messages=moderator,
                                        ban_users=moderator, invite_users=moderator,
                                        invite_link=moderator, pin_messages=moderator,
                                        add_admins=admin)
            await sender.client(
                EditAdminRequest(channel=await self.get_input_entity(sender),
                                 user_id=user_id, admin_rights=rights))

    async def handle_matrix_power_levels(self, sender, new_users, old_users):
        # TODO handle all power level changes and bridge exact admin rights to supergroups/channels
        for user, level in new_users.items():
            if not user or user == self.main_intent.mxid or user == sender.mxid:
                continue
            user_id = p.Puppet.get_id_from_mxid(user)
            if not user_id:
                mx_user = u.User.get_by_mxid(user, create=False)
                if not mx_user or not mx_user.tgid:
                    continue
                user_id = mx_user.tgid
            if not user_id or user_id == sender.tgid:
                continue
            if user not in old_users or level != old_users[user]:
                await self._update_telegram_power_level(sender, user_id, level)

    async def handle_matrix_about(self, sender, about):
        if self.peer_type not in {"channel"}:
            return
        channel = await self.get_input_entity(sender)
        await sender.client(EditAboutRequest(channel=channel, about=about))
        self.about = about
        self.save()

    async def handle_matrix_title(self, sender, title):
        if self.peer_type not in {"chat", "channel"}:
            return

        if self.peer_type == "chat":
            response = await sender.client(EditChatTitleRequest(chat_id=self.tgid, title=title))
        else:
            channel = await self.get_input_entity(sender)
            response = await sender.client(EditTitleRequest(channel=channel, title=title))
        self._register_outgoing_actions_for_dedup(response)
        self.title = title
        self.save()

    async def handle_matrix_avatar(self, sender, url):
        if self.peer_type not in {"chat", "channel"}:
            # Invalid peer type
            return

        file = await self.main_intent.download_file(url)
        mime = magic.from_buffer(file, mime=True)
        ext = mimetypes.guess_extension(mime)
        uploaded = await sender.client.upload_file(file, file_name=f"avatar{ext}")
        photo = InputChatUploadedPhoto(file=uploaded)

        if self.peer_type == "chat":
            response = await sender.client(EditChatPhotoRequest(chat_id=self.tgid, photo=photo))
        else:
            channel = await self.get_input_entity(sender)
            response = await sender.client(EditPhotoRequest(channel=channel, photo=photo))
        self._register_outgoing_actions_for_dedup(response)
        for update in response.updates:
            is_photo_update = (isinstance(update, UpdateNewMessage)
                               and isinstance(update.message, MessageService)
                               and isinstance(update.message.action, MessageActionChatEditPhoto))
            if is_photo_update:
                loc = self._get_largest_photo_size(update.message.action.photo).location
                self.photo_id = f"{loc.volume_id}-{loc.local_id}"
                self.save()
                break

    def _register_outgoing_actions_for_dedup(self, response):
        for update in response.updates:
            check_dedup = (isinstance(update, (UpdateNewMessage, UpdateNewChannelMessage))
                           and isinstance(update.message, MessageService))
            if check_dedup:
                self.is_duplicate_action(update.message)

    # endregion
    # region Telegram chat info updating

    async def _get_telegram_users_in_matrix_room(self):
        user_tgids = set()
        user_mxids = await self.main_intent.get_room_members(self.mxid, ("join", "invite"))
        for user in user_mxids:
            if user == self.az.bot_mxid:
                continue
            mx_user = u.User.get_by_mxid(user, create=False)
            if mx_user and mx_user.tgid:
                user_tgids.add(mx_user.tgid)
            puppet_id = p.Puppet.get_id_from_mxid(user)
            if puppet_id:
                user_tgids.add(puppet_id)
        return list(user_tgids)

    async def upgrade_telegram_chat(self, source):
        if self.peer_type != "chat":
            raise ValueError("Only normal group chats are upgradable to supergroups.")

        updates = await source.client(MigrateChatRequest(chat_id=self.tgid))
        entity = None
        for chat in updates.chats:
            if isinstance(chat, Channel):
                entity = chat
                break
        if not entity:
            raise ValueError("Upgrade may have failed: output channel not found.")
        self.peer_type = "channel"
        self.migrate_and_save(entity.id)
        await self.update_info(source, entity)

    async def set_telegram_username(self, source, username):
        if self.peer_type != "channel":
            raise ValueError("Only channels and supergroups have usernames.")
        await source.client(
            UpdateUsernameRequest(await self.get_input_entity(source), username))
        if await self.update_username(username):
            self.save()

    async def create_telegram_chat(self, source, supergroup=False):
        if not self.mxid:
            raise ValueError("Can't create Telegram chat for portal without Matrix room.")
        elif self.tgid:
            raise ValueError("Can't create Telegram chat for portal with existing Telegram chat.")

        invites = await self._get_telegram_users_in_matrix_room()
        if len(invites) < 2:
            raise ValueError("Not enough Telegram users to create a chat")

        if self.peer_type == "chat":
            updates = await source.client(CreateChatRequest(title=self.title, users=invites))
            entity = updates.chats[0]
        elif self.peer_type == "channel":
            updates = await source.client(CreateChannelRequest(title=self.title,
                                                               about=self.about or "",
                                                               megagroup=supergroup))
            entity = updates.chats[0]
            await source.client(InviteToChannelRequest(
                channel=await source.client.get_input_entity(entity),
                users=invites))
        else:
            raise ValueError("Invalid peer type for Telegram chat creation")

        self.tgid = entity.id
        self.tg_receiver = self.tgid
        self.by_tgid[self.tgid_full] = self
        await self.update_info(source, entity)
        self.db.add(self.db_instance)
        self.save()

        if self.bot and self.bot.tgid in invites:
            self.bot.add_chat(self.tgid, self.peer_type)

        levels = await self.main_intent.get_power_levels(self.mxid)
        bot_level = self._get_bot_level(levels)
        if bot_level == 100:
            levels = self._get_base_power_levels(levels, entity)
            await self.main_intent.set_power_levels(self.mxid, levels)
        await self.handle_matrix_power_levels(source, levels["users"], {})

    async def invite_telegram(self, source, puppet):
        if self.peer_type == "chat":
            await source.client(
                AddChatUserRequest(chat_id=self.tgid, user_id=puppet.tgid, fwd_limit=0))
        elif self.peer_type == "channel":
            await source.client(InviteToChannelRequest(channel=self.peer, users=[puppet.tgid]))
        else:
            raise ValueError("Invalid peer type for Telegram user invite")

    # endregion
    # region Telegram event handling

    async def handle_telegram_typing(self, user, event):
        if self.mxid:
            await user.intent.set_typing(self.mxid, is_typing=True)

    def get_external_url(self, evt: Message):
        if self.peer_type == "channel" and self.username is not None:
            return f"https://t.me/{self.username}/{evt.id}"
        return None

    async def handle_telegram_photo(self, source: u.User, intent, evt: Message, relates_to=None):
        largest_size = self._get_largest_photo_size(evt.media.photo)
        file = await util.transfer_file_to_matrix(self.db, source.client, intent,
                                                  largest_size.location)
        if not file:
            return None
        if config["bridge.inline_images"] and (evt.message or evt.fwd_from or evt.reply_to_msg_id):
            text, html, relates_to = await formatter.telegram_to_matrix(
                evt, source, self.main_intent,
                prefix_html=f"<img src='{file.mxc}' alt='Inline Telegram photo'/><br/>",
                prefix_text="Inline image: ")
            await intent.set_typing(self.mxid, is_typing=False)
            return await intent.send_text(self.mxid, text, html=html, relates_to=relates_to,
                                          timestamp=evt.date,
                                          external_url=self.get_external_url(evt))
        info = {
            "h": largest_size.h,
            "w": largest_size.w,
            "size": len(largest_size.bytes) if (
                isinstance(largest_size, PhotoCachedSize)) else largest_size.size,
            "orientation": 0,
            "mimetype": file.mime_type,
        }
        name = evt.message
        await intent.set_typing(self.mxid, is_typing=False)
        return await intent.send_image(self.mxid, file.mxc, info=info, text=name,
                                       relates_to=relates_to, timestamp=evt.date,
                                       external_url=self.get_external_url(evt))

    @staticmethod
    def _parse_telegram_document_attributes(attributes):
        attrs = {
            "name": None,
            "mime_type": None,
            "is_sticker": False,
            "sticker_alt": None,
            "width": None,
            "height": None,
        }
        for attr in attributes:
            if isinstance(attr, DocumentAttributeFilename):
                attrs["name"] = attrs["name"] or attr.file_name
                attrs["mime_type"], _ = mimetypes.guess_type(attr.file_name)
            elif isinstance(attr, DocumentAttributeSticker):
                attrs["is_sticker"] = True
                attrs["sticker_alt"] = attr.alt
            elif isinstance(attr, DocumentAttributeVideo):
                attrs["width"], attrs["height"] = attr.w, attr.h
        return attrs

    @staticmethod
    def _parse_telegram_document_meta(evt, file, attrs):
        document = evt.media.document
        name = evt.message or attrs["name"]
        if attrs["is_sticker"]:
            alt = attrs["sticker_alt"]
            if len(alt) > 0:
                name = f"{alt} ({unicodedata.name(alt[0]).lower()})"

        mime_type = document.mime_type or file.mime_type
        info = {
            "size": file.size,
            "mimetype": mime_type,
        }

        if attrs["mime_type"] and not file.was_converted:
            file.mime_type = attrs["mime_type"] or file.mime_type
        if file.width and file.height:
            info["w"], info["h"] = file.width, file.height
        elif attrs["width"] and attrs["height"]:
            info["w"], info["h"] = attrs["width"], attrs["height"]

        if file.thumbnail:
            info["thumbnail_url"] = file.thumbnail.mxc
            info["thumbnail_info"] = {
                "mimetype": file.thumbnail.mime_type,
                "h": file.thumbnail.height or document.thumb.h,
                "w": file.thumbnail.width or document.thumb.w,
                "size": file.thumbnail.size,
            }

        return info, name

    async def handle_telegram_document(self, source, intent, evt: Message, relates_to=None):
        document = evt.media.document
        attrs = self._parse_telegram_document_attributes(document.attributes)

        file = await util.transfer_file_to_matrix(self.db, source.client, intent, document,
                                                  document.thumb, is_sticker=attrs["is_sticker"])
        if not file:
            return None

        info, name = self._parse_telegram_document_meta(evt, file, attrs)

        await intent.set_typing(self.mxid, is_typing=False)

        kwargs = {
            "room_id": self.mxid,
            "url": file.mxc,
            "info": info,
            "text": name,
            "relates_to": relates_to,
            "timestamp": evt.date,
            "external_url": self.get_external_url(evt)
        }

        if attrs["is_sticker"] and config["bridge.native_stickers"]:
            return await intent.send_sticker(**kwargs)

        mime_type = info["mimetype"]
        if mime_type.startswith("video/"):
            kwargs["file_type"] = "m.video"
        elif mime_type.startswith("audio/"):
            kwargs["file_type"] = "m.audio"
        elif mime_type.startswith("image/"):
            kwargs["file_type"] = "m.image"
        else:
            kwargs["file_type"] = "m.file"
        return await intent.send_file(**kwargs)

    def handle_telegram_location(self, source, intent, evt, relates_to=None):
        location = evt.media.geo
        long = location.long
        lat = location.lat
        long_char = "E" if long > 0 else "W"
        lat_char = "N" if lat > 0 else "S"
        rounded_long = abs(round(long * 100000) / 100000)
        rounded_lat = abs(round(lat * 100000) / 100000)

        body = f"{rounded_lat}° {lat_char}, {rounded_long}° {long_char}"

        url = f"https://maps.google.com/?q={lat},{long}"

        formatted_body = f"Location: <a href='{url}'>{body}</a>"
        # At least riot-web ignores formatting in m.location messages,
        # so we'll add a plaintext link.
        body = f"Location: {body}\n{url}"

        return intent.send_message(self.mxid, {
            "msgtype": "m.location",
            "geo_uri": f"geo:{lat},{long}",
            "body": body,
            "format": "org.matrix.custom.html",
            "formatted_body": formatted_body,
            "m.relates_to": relates_to or None,
        }, timestamp=evt.date, external_url=self.get_external_url(evt))

    async def handle_telegram_text(self, source, intent, is_bot, evt):
        self.log.debug(f"Sending {evt.message} to {self.mxid} by {intent.mxid}")
        text, html, relates_to = await formatter.telegram_to_matrix(evt, source, self.main_intent)
        await intent.set_typing(self.mxid, is_typing=False)
        msgtype = "m.notice" if is_bot and config["bridge.bot_messages_as_notices"] else "m.text"
        return await intent.send_text(self.mxid, text, html=html, relates_to=relates_to,
                                      msgtype=msgtype, timestamp=evt.date,
                                      external_url=self.get_external_url(evt))

    async def handle_telegram_edit(self, source, sender, evt):
        if not self.mxid:
            return
        elif not config["bridge.edits_as_replies"]:
            self.log.debug("Edits as replies disabled, ignoring edit event...")
            return

        lock = self.optional_send_lock(sender.tgid if sender else None)
        if lock:
            async with lock:
                pass

        tg_space = self.tgid if self.peer_type == "channel" else source.tgid

        temporary_identifier = f"${random.randint(1000000000000,9999999999999)}TGBRIDGEDITEMP"
        duplicate_found = self.is_duplicate(evt, (temporary_identifier, tg_space), force_hash=True)
        if duplicate_found:
            mxid, other_tg_space = duplicate_found
            if tg_space != other_tg_space:
                msg = DBMessage.query.get((evt.id, tg_space))
                msg.mxid = mxid
                msg.mx_room = self.mxid
                self.db.commit()
            return

        evt.reply_to_msg_id = evt.id
        text, html, relates_to = await formatter.telegram_to_matrix(evt, source, self.main_intent,
                                                                    is_edit=True)
        intent = sender.intent if sender else self.main_intent
        await intent.set_typing(self.mxid, is_typing=False)
        response = await intent.send_text(self.mxid, text, html=html, relates_to=relates_to,
                                          external_url=self.get_external_url(evt))

        mxid = response["event_id"]

        msg = DBMessage.query.get((evt.id, tg_space))
        if not msg:
            # Oh crap
            return
        msg.mxid = mxid
        msg.mx_room = self.mxid
        DBMessage.query \
            .filter(DBMessage.mx_room == self.mxid,
                    DBMessage.mxid == temporary_identifier) \
            .update({"mxid": mxid})
        self.db.commit()

    async def handle_telegram_message(self, source, sender, evt):
        if not self.mxid:
            await self.create_matrix_room(source, invites=[source.mxid], update_if_exists=False)

        lock = self.optional_send_lock(sender.tgid if sender else None)
        if lock:
            async with lock:
                pass

        tg_space = self.tgid if self.peer_type == "channel" else source.tgid

        temporary_identifier = f"${random.randint(1000000000000,9999999999999)}TGBRIDGETEMP"
        duplicate_found = self.is_duplicate(evt, (temporary_identifier, tg_space))
        if duplicate_found:
            mxid, other_tg_space = duplicate_found
            if tg_space != other_tg_space:
                self.db.add(
                    DBMessage(tgid=evt.id, mx_room=self.mxid, mxid=mxid, tg_space=tg_space))
                self.db.commit()
            return

        if sender and not sender.displayname:
            self.log.debug(f"Telegram user {sender.tgid} sent a message, but doesn't have a"
                           "displayname, updating info...")
            entity = await source.client.get_entity(PeerUser(sender.tgid))
            await sender.update_info(source, entity)

        allowed_media = (MessageMediaPhoto, MessageMediaDocument, MessageMediaGeo)
        media = evt.media if hasattr(evt, "media") and isinstance(evt.media,
                                                                  allowed_media) else None
        intent = sender.intent if sender else self.main_intent
        if not media and evt.message:
            is_bot = sender.is_bot if sender else False
            response = await self.handle_telegram_text(source, intent, is_bot, evt)
        elif media:
            relates_to = formatter.telegram_reply_to_matrix(evt, source)
            if isinstance(media, MessageMediaPhoto):
                response = await self.handle_telegram_photo(source, intent, evt, relates_to)
            elif isinstance(media, MessageMediaDocument):
                response = await self.handle_telegram_document(source, intent, evt, relates_to)
            elif isinstance(media, MessageMediaGeo):
                response = await self.handle_telegram_location(source, intent, evt, relates_to)
            else:
                self.log.debug("Unhandled Telegram media: %s", media)
                return
        else:
            self.log.debug("Unhandled Telegram message: %s", evt)
            return

        if not response:
            return

        mxid = response["event_id"]

        prev_id = self.update_duplicate(evt, (mxid, tg_space), (temporary_identifier, tg_space))
        if prev_id:
            self.log.debug(f"Sent message {evt.id}@{tg_space} to Matrix as {mxid}. "
                           f"Temporary dedup identifier was {temporary_identifier}, "
                           f"but dedup map contained {prev_id[1]} instead! -- "
                           "This was probably a race condition caused by Telegram sending updates"
                           "to other clients before responding to the sender. I'll just redact "
                           "the likely duplicate message now.")
            await intent.redact(self.mxid, mxid)
            return

        self.log.debug("Handled Telegram message: %s", evt)
        try:
            self.db.add(DBMessage(tgid=evt.id, mx_room=self.mxid, mxid=mxid, tg_space=tg_space))
            self.db.commit()
            DBMessage.query \
                .filter(DBMessage.mx_room == self.mxid,
                        DBMessage.mxid == temporary_identifier) \
                .update({"mxid": mxid})
        except FlushError as e:
            self.log.exception(f"{e.__class__.__name__} while saving message mapping.")
            await intent.redact(self.mxid, mxid)
        except (IntegrityError, InvalidRequestError) as e:
            self.log.exception(f"{e.__class__.__name__} while saving message mapping.")
            self.db.rollback()
            await intent.redact(self.mxid, mxid)

    async def _create_room_on_action(self, source, action):
        if source.is_relaybot:
            return False
        create_and_exit = (MessageActionChatCreate, MessageActionChannelCreate)
        create_and_continue = (MessageActionChatAddUser, MessageActionChatJoinedByLink)
        if isinstance(action, create_and_exit + create_and_continue):
            await self.create_matrix_room(source, invites=[source.mxid],
                                          update_if_exists=isinstance(action, create_and_exit))
        if not isinstance(action, create_and_continue):
            return False
        return True

    async def handle_telegram_action(self, source, sender, update):
        action = update.action
        should_ignore = ((not self.mxid and not await self._create_room_on_action(source, action))
                         or self.is_duplicate_action(update))
        if should_ignore or not self.mxid:
            return
        # TODO figure out how to see changes to about text / channel username
        if isinstance(action, MessageActionChatEditTitle):
            await self.update_title(action.title, save=True)
        elif isinstance(action, MessageActionChatEditPhoto):
            largest_size = self._get_largest_photo_size(action.photo)
            await self.update_avatar(source, largest_size.location, save=True)
        elif isinstance(action, MessageActionChatDeletePhoto):
            await self.remove_avatar(source, save=True)
        elif isinstance(action, MessageActionChatAddUser):
            for user_id in action.users:
                await self.add_telegram_user(user_id, source)
        elif isinstance(action, MessageActionChatJoinedByLink):
            await self.add_telegram_user(sender.id, source)
        elif isinstance(action, MessageActionChatDeleteUser):
            await self.delete_telegram_user(action.user_id, sender)
        elif isinstance(action, MessageActionChatMigrateTo):
            self.peer_type = "channel"
            self.migrate_and_save(action.channel_id)
            await sender.intent.send_emote(self.mxid, "upgraded this group to a supergroup.")
        elif isinstance(action, MessageActionPinMessage):
            await self.receive_telegram_pin_sender(sender)
        else:
            self.log.debug("Unhandled Telegram action in %s: %s", self.title, action)

    async def set_telegram_admin(self, user_id):
        puppet = p.Puppet.get(user_id)
        user = await u.User.get_by_tgid(user_id)

        levels = await self.main_intent.get_power_levels(self.mxid)
        if user:
            levels["users"][user.mxid] = 50
        if puppet:
            levels["users"][puppet.mxid] = 50
        await self.main_intent.set_power_levels(self.mxid, levels)

    async def receive_telegram_pin_sender(self, sender):
        self._temp_pinned_message_sender = sender
        if self._temp_pinned_message_id:
            await self.update_telegram_pin()

    async def update_telegram_pin(self):
        intent = (self._temp_pinned_message_sender.intent
                  if self._temp_pinned_message_sender else self.main_intent)
        id = self._temp_pinned_message_id
        self._temp_pinned_message_id = None
        self._temp_pinned_message_sender = None

        message = DBMessage.query.get((id, self.tgid))
        if message:
            await intent.set_pinned_messages(self.mxid, [message.mxid])
        else:
            await intent.set_pinned_messages(self.mxid, [])

    async def receive_telegram_pin_id(self, id):
        if id == 0:
            return await self.update_telegram_pin()
        self._temp_pinned_message_id = id
        if self._temp_pinned_message_sender:
            await self.update_telegram_pin()

    @staticmethod
    def _get_level_from_participant(participant, _):
        # TODO use the power level requirements to get better precision in channels
        if isinstance(participant, (ChatParticipantAdmin, ChannelParticipantAdmin)):
            return 50
        elif isinstance(participant, (ChatParticipantCreator, ChannelParticipantCreator)):
            return 95
        return 0

    @staticmethod
    def _participant_to_power_levels(levels, user, new_level, bot_level):
        new_level = min(new_level, bot_level)
        user_level_defined = user.mxid in levels["users"]
        default_level = levels["users_default"] if "users_default" in levels else 0
        user_level = levels["users"][user.mxid] if user_level_defined else default_level
        if user_level != new_level and user_level < bot_level:
            levels["users"][user.mxid] = new_level
            return True
        return False

    def _get_bot_level(self, levels):
        try:
            return levels["users"][self.main_intent.mxid]
        except KeyError:
            try:
                return levels["users_default"]
            except KeyError:
                return 0

    @staticmethod
    def _get_powerlevel_level(levels):
        try:
            return levels["events"]["m.room.power_levels"]
        except KeyError:
            try:
                return levels["state_default"]
            except KeyError:
                return 50

    def _participants_to_power_levels(self, participants, levels):
        bot_level = self._get_bot_level(levels)
        if bot_level < self._get_powerlevel_level(levels):
            return False
        changed = False
        admin_power_level = min(75 if self.peer_type == "channel" else 50, bot_level)
        if levels["events"]["m.room.power_levels"] != admin_power_level:
            changed = True
            levels["events"]["m.room.power_levels"] = admin_power_level

        for participant in participants:
            puppet = p.Puppet.get(participant.user_id)
            user = u.User.get_by_tgid(participant.user_id)
            new_level = self._get_level_from_participant(participant, levels)

            if user:
                user.register_portal(self)
                changed = self._participant_to_power_levels(levels, user, new_level,
                                                            bot_level) or changed

            if puppet:
                changed = self._participant_to_power_levels(levels, puppet, new_level,
                                                            bot_level) or changed
        return changed

    async def update_telegram_participants(self, participants, levels=None):
        if not levels:
            levels = await self.main_intent.get_power_levels(self.mxid)
        if self._participants_to_power_levels(participants, levels):
            await self.main_intent.set_power_levels(self.mxid, levels)

    async def set_telegram_admins_enabled(self, enabled):
        level = 50 if enabled else 10
        levels = await self.main_intent.get_power_levels(self.mxid)
        levels["invite"] = level
        levels["events"]["m.room.name"] = level
        levels["events"]["m.room.avatar"] = level
        await self.main_intent.set_power_levels(self.mxid, levels)

    # endregion
    # region Database conversion

    @property
    def db_instance(self):
        if not self._db_instance:
            self._db_instance = self.new_db_instance()
        return self._db_instance

    def new_db_instance(self):
        return DBPortal(tgid=self.tgid, tg_receiver=self.tg_receiver, peer_type=self.peer_type,
                        mxid=self.mxid, username=self.username, megagroup=self.megagroup,
                        title=self.title, about=self.about, photo_id=self.photo_id)

    def migrate_and_save(self, new_id):
        existing = DBPortal.query.get(self.tgid_full)
        if existing:
            self.db.delete(existing)
        try:
            del self.by_tgid[self.tgid_full]
        except KeyError:
            pass
        self.tgid = new_id
        self.tg_receiver = new_id
        self.by_tgid[self.tgid_full] = self
        self.save()

    def save(self):
        self.db_instance.mxid = self.mxid
        self.db_instance.username = self.username
        self.db_instance.title = self.title
        self.db_instance.about = self.about
        self.db_instance.photo_id = self.photo_id
        self.db.commit()

    def delete(self):
        try:
            del self.by_tgid[self.tgid_full]
        except KeyError:
            pass
        try:
            del self.by_mxid[self.mxid]
        except KeyError:
            pass
        if self._db_instance:
            self.db.delete(self._db_instance)
            self.db.commit()

    @classmethod
    def from_db(cls, db_portal):
        return Portal(tgid=db_portal.tgid, tg_receiver=db_portal.tg_receiver,
                      peer_type=db_portal.peer_type, mxid=db_portal.mxid,
                      username=db_portal.username, megagroup=db_portal.megagroup,
                      title=db_portal.title, about=db_portal.about, photo_id=db_portal.photo_id,
                      db_instance=db_portal)

    # endregion
    # region Class instance lookup

    @classmethod
    def get_by_mxid(cls, mxid):
        try:
            return cls.by_mxid[mxid]
        except KeyError:
            pass

        portal = DBPortal.query.filter(DBPortal.mxid == mxid).one_or_none()
        if portal:
            return cls.from_db(portal)

        return None

    @classmethod
    def get_username_from_mx_alias(cls, alias):
        match = cls.mx_alias_regex.match(alias)
        if match:
            return match.group(1)
        return None

    @classmethod
    def find_by_username(cls, username):
        if not username:
            return None

        for _, portal in cls.by_tgid.items():
            if portal.username and portal.username.lower() == username.lower():
                return portal

        portal = DBPortal.query.filter(DBPortal.username == username).one_or_none()
        if portal:
            return cls.from_db(portal)

        return None

    @classmethod
    def get_by_tgid(cls, tgid, tg_receiver=None, peer_type=None):
        tg_receiver = tg_receiver or tgid
        tgid_full = (tgid, tg_receiver)
        try:
            return cls.by_tgid[tgid_full]
        except KeyError:
            pass

        portal = DBPortal.query.get(tgid_full)
        if portal:
            return cls.from_db(portal)

        if peer_type:
            portal = Portal(tgid, peer_type=peer_type, tg_receiver=tg_receiver)
            cls.db.add(portal.db_instance)
            cls.db.commit()
            return portal

        return None

    @classmethod
    def get_by_entity(cls, entity, receiver_id=None, create=True):
        entity_type = type(entity)
        if entity_type in {Chat, ChatFull}:
            type_name = "chat"
            id = entity.id
        elif entity_type in {PeerChat, InputPeerChat}:
            type_name = "chat"
            id = entity.chat_id
        elif entity_type in {Channel, ChannelFull}:
            type_name = "channel"
            id = entity.id
        elif entity_type in {PeerChannel, InputPeerChannel, InputChannel}:
            type_name = "channel"
            id = entity.channel_id
        elif entity_type in {User, UserFull}:
            type_name = "user"
            id = entity.id
        elif entity_type in {PeerUser, InputPeerUser, InputUser}:
            type_name = "user"
            id = entity.user_id
        else:
            raise ValueError(f"Unknown entity type {entity_type.__name__}")
        return cls.get_by_tgid(id,
                               receiver_id if type_name == "user" else id,
                               type_name if create else None)

    # endregion


def init(context):
    global config
    Portal.az, Portal.db, config, Portal.loop, Portal.bot = context
    Portal.bridge_notices = config["bridge.bridge_notices"]
    Portal.filter_mode = config["bridge.filter.mode"]
    Portal.filter_list = config["bridge.filter.list"]
    Portal.alias_template = config.get("bridge.alias_template", "telegram_{groupname}")
    Portal.hs_domain = config["homeserver.domain"]
    localpart = Portal.alias_template.format(groupname="(.+)")
    Portal.mx_alias_regex = re.compile(f"#{localpart}:{Portal.hs_domain}")
