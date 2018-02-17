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
from io import BytesIO
from collections import deque
from datetime import datetime
import asyncio
import random
import mimetypes
import hashlib
import logging

from PIL import Image
import magic

from telethon.tl.functions.messages import *
from telethon.tl.functions.channels import *
from telethon.errors.rpc_error_list import *
from telethon.tl.types import *
from mautrix_appservice import MatrixRequestError, IntentError

from .db import Portal as DBPortal, Message as DBMessage
from . import puppet as p, user as u, formatter

mimetypes.init()

config = None


class Portal:
    log = logging.getLogger("mau.portal")
    db = None
    az = None
    by_mxid = {}
    by_tgid = {}

    def __init__(self, tgid, peer_type, tg_receiver=None, mxid=None, username=None, title=None,
                 about=None, photo_id=None, save_to_cache=True):
        self.mxid = mxid
        self.tgid = tgid
        self.tg_receiver = tg_receiver or tgid
        self.peer_type = peer_type
        self.username = username
        self.title = title
        self.about = about
        self.photo_id = photo_id
        self._main_intent = None
        self._room_create_lock = asyncio.Lock()

        self._dedup = deque()
        self._dedup_mxid = {}

        if save_to_cache:
            if tgid:
                self.by_tgid[self.tgid_full] = self
            if mxid:
                self.by_mxid[mxid] = self

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

    def _hash_event(self, event):
        if self.peer_type == "channel":
            # Message IDs are unique per-channel
            return event.id

        # Non-channel messages are unique per-user (wtf telegram), so we have no other choice than
        # to deduplicate based on a hash of the message content.

        # The timestamp is only accurate to the second, so we can't rely on solely that either.
        hash_content = [event.date.timestamp(), event.message]
        if event.fwd_from:
            hash_content += [event.fwd_from.from_id, event.fwd_from.channel_id]
        elif isinstance(event, Message) and event.media:
            try:
                hash_content += {
                    MessageMediaContact: lambda media: [media.user_id],
                    MessageMediaDocument: lambda media: [media.document.id, media.caption],
                    MessageMediaPhoto: lambda media: [media.photo.id, media.caption],
                    MessageMediaGeo: lambda media: [media.geo.long, media.geo.lat],
                }[type(event.media)](event.media)
            except KeyError:
                pass

        return hashlib.md5("-"
                           .join(str(a) for a in hash_content)
                           .encode("utf-8")
                           ).hexdigest()

    def is_duplicate(self, event, mxid=None):
        hash = self._hash_event(event)
        if hash in self._dedup:
            return self._dedup_mxid[hash]

        self._dedup_mxid[hash] = mxid
        self._dedup.append(hash)

        if len(self._dedup) > 20:
            del self._dedup_mxid[self._dedup.popleft()]
        return None

    def get_input_entity(self, user):
        return user.client.get_input_entity(self.peer)

    # region Matrix room info updating

    @property
    def main_intent(self):
        if not self._main_intent:
            direct = self.peer_type == "user"
            puppet = p.Puppet.get(self.tgid) if direct else None
            self._main_intent = puppet.intent if direct else self.az.intent
        return self._main_intent

    async def invite_matrix(self, users):
        if isinstance(users, str):
            await self.main_intent.invite(self.mxid, users, check_cache=True)
        elif isinstance(users, list):
            for user in users:
                await self.main_intent.invite(self.mxid, user, check_cache=True)
        else:
            raise ValueError("Invalid invite identifier given to invite_matrix()")

    async def update_after_create(self, user, entity, direct, puppet=None,
                                  levels=None, users=None, participants=None):
        if not direct:
            await self.update_info(user, entity)
            if not users or not participants:
                users, participants = await self.get_users(user, entity)
            await self.sync_telegram_users(user, users)
            await self.update_telegram_participants(participants, levels)
        else:
            if not puppet:
                puppet = p.Puppet.get(self.tgid)
            await puppet.update_info(user, entity)
            await puppet.intent.join_room(self.mxid)

    async def create_matrix_room(self, user, entity=None, invites=None, update_if_exists=True):
        if self.mxid:
            if update_if_exists:
                if not entity:
                    entity = await user.client.get_entity(self.peer)
                await self.update_after_create(user, entity, self.peer_type == "user")
                await self.invite_matrix(invites or [])
            return self.mxid
        async with self._room_create_lock:
            return await self._create_matrix_room(user, entity, invites)

    async def _create_matrix_room(self, user, entity, invites):
        direct = self.peer_type == "user"

        if self.mxid:
            return self.mxid

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

        if self.peer_type == "channel" and entity.username:
            # TODO make public once safe
            public = False
            alias = self._get_room_alias(entity.username)
            self.username = entity.username
        else:
            public = False
            # TODO invite link alias?
            alias = None

        if alias:
            # TODO properly handle existing room aliases
            await self.main_intent.remove_room_alias(alias)

        power_levels = self._get_base_power_levels({}, entity)
        users = participants = None
        if not direct:
            users, participants = await self.get_users(user, entity)
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
        await self.update_after_create(user, entity, direct, puppet,
                                       levels=power_levels, users=users, participants=participants)

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
        levels["events_default"] = (50 if self.peer_type == "channel" and not entity.megagroup
                                    else 0)
        if "users" not in levels:
            levels["users"] = {
                self.main_intent.mxid: 100
            }
        return levels

    def _get_room_alias(self, username=None):
        username = username or self.username
        return config.get("bridge.alias_template", "telegram_{groupname}").format(
            groupname=username)

    async def sync_telegram_users(self, source, users):
        for entity in users:
            puppet = p.Puppet.get(entity.id)
            await puppet.intent.ensure_joined(self.mxid)
            await puppet.update_info(source, entity)

    async def add_telegram_user(self, user_id, source=None):
        puppet = p.Puppet.get(user_id)
        if source:
            entity = await source.client.get_entity(user_id)
            await puppet.update_info(source, entity)
            await puppet.intent.join_room(self.mxid)

        user = u.User.get_by_tgid(user_id)
        if user:
            user.register_portal(self)
            await self.main_intent.invite(self.mxid, user.mxid)

    async def delete_telegram_user(self, user_id, kick_message=None):
        puppet = p.Puppet.get(user_id)
        user = u.User.get_by_tgid(user_id)
        if kick_message:
            await self.main_intent.kick(self.mxid, puppet.mxid, kick_message)
        else:
            await puppet.intent.leave_room(self.mxid)
        if user:
            user.unregister_portal(self)
            await self.main_intent.kick(self.mxid, user.mxid, kick_message or "Left Telegram chat")

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

    async def update_username(self, username):
        if self.username != username:
            if self.username:
                await self.main_intent.remove_room_alias(self._get_room_alias())
            self.username = username or None
            if self.username:
                await self.main_intent.add_room_alias(self.mxid, self._get_room_alias())
            return True
        return False

    async def update_about(self, about):
        if self.about != about:
            self.about = about
            await self.main_intent.set_room_topic(self.mxid, self.about)
            return True
        return False

    async def update_title(self, title):
        if self.title != title:
            self.title = title
            await self.main_intent.set_room_name(self.mxid, self.title)
            return True
        return False

    @staticmethod
    def _get_largest_photo_size(photo):
        return max(photo.sizes, key=(lambda photo2: (
            len(photo2.bytes) if isinstance(photo2, PhotoCachedSize) else photo2.size)))

    async def update_avatar(self, user, photo):
        photo_id = f"{photo.volume_id}-{photo.local_id}"
        if self.photo_id != photo_id:
            try:
                file = await user.client.download_file_bytes(photo)
            except LocationInvalidError:
                return False
            uploaded = await self.main_intent.upload_file(file)
            await self.main_intent.set_room_avatar(self.mxid, uploaded["content_uri"])
            self.photo_id = photo_id
            return True
        return False

    async def get_users(self, user, entity):
        if self.peer_type == "chat":
            chat = await user.client(GetFullChatRequest(chat_id=self.tgid))
            return chat.users, chat.full_chat.participants.participants
        elif self.peer_type == "channel":
            try:
                participants = await user.client(GetParticipantsRequest(
                    entity, ChannelParticipantsRecent(), offset=0, limit=100, hash=0
                ))
                return participants.users, participants.participants
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
        for member in members:
            if p.Puppet.get_id_from_mxid(member) or member == self.main_intent.mxid:
                continue
            user = u.User.get_by_mxid(member)
            if user.has_full_access:
                authenticated.append(user)
        return authenticated

    @staticmethod
    async def cleanup_room(intent, room_id, type="Portal"):
        try:
            members = await intent.get_room_members(room_id)
        except MatrixRequestError:
            members = []
        for user in members:
            if user != intent.mxid:
                try:
                    await intent.kick(room_id, user, f"{type} deleted.")
                except (MatrixRequestError, IntentError):
                    pass
        await intent.leave_room(room_id)

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
                file_name = body
            else:
                file_name = f"matrix_upload{mimetypes.guess_extension(mime)}"
        except (ValueError, KeyError):
            file_name = f"matrix_upload{mimetypes.guess_extension(mime)}"
        return file_name, None if file_name == body else body

    async def leave_matrix(self, user, source):
        if self.peer_type == "user":
            await self.main_intent.leave_room(self.mxid)
            self.delete()
            del self.by_tgid[self.tgid_full]
            del self.by_mxid[self.mxid]
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

    async def handle_matrix_message(self, sender, message, event_id):
        type = message["msgtype"]
        space = self.tgid if self.peer_type == "channel" else sender.tgid
        reply_to = formatter.matrix_reply_to_telegram(message, space, room_id=self.mxid)
        if type in {"m.text", "m.emote"}:
            if "format" in message and message["format"] == "org.matrix.custom.html":
                message, entities = formatter.matrix_to_telegram(message["formatted_body"], space)
                if type == "m.emote":
                    message = "/me " + message
                response = await sender.client.send_message(self.peer, message, entities=entities,
                                                            reply_to=reply_to)
            else:
                if type == "m.emote":
                    message["body"] = "/me " + message["body"]
                response = await sender.client.send_message(self.peer, message["body"],
                                                            reply_to=reply_to)
        elif type in {"m.image", "m.file", "m.audio", "m.video"}:
            file = await self.main_intent.download_file(message["url"])

            info = message["info"]
            mime = info["mimetype"]

            file_name, caption = self._get_file_meta(message["body"], mime)

            attributes = [DocumentAttributeFilename(file_name=file_name)]
            if "w" in info and "h" in info:
                attributes.append(DocumentAttributeImageSize(w=info["w"], h=info["h"]))

            response = await sender.client.send_file(self.peer, file, mime, caption, attributes,
                                                     file_name, reply_to=reply_to)
        else:
            self.log.debug("Unhandled Matrix event: %s", message)
            return
        self.is_duplicate(response, (event_id, space))
        self.db.add(DBMessage(
            tgid=response.id,
            tg_space=space,
            mx_room=self.mxid,
            mxid=event_id))
        self.db.commit()

    async def handle_matrix_deletion(self, deleter, event_id):
        space = self.tgid if self.peer_type == "channel" else deleter.tgid
        message = DBMessage.query.filter(DBMessage.mxid == event_id and
                                         DBMessage.tg_space == space and
                                         DBMessage.mx_room == self.mxid).one_or_none()
        if not message:
            return
        await deleter.client.delete_messages(self.peer, [message.tgid])

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
            if user not in old_users or level != old_users[user]:
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
                                                add_admins=admin, manage_call=moderator)
                    await sender.client(
                        EditAdminRequest(channel=await self.get_input_entity(sender),
                                         user_id=user_id, admin_rights=rights))

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
            await sender.client(EditChatTitleRequest(chat_id=self.tgid, title=title))
        else:
            channel = await self.get_input_entity(sender)
            await sender.client(EditTitleRequest(channel=channel, title=title))
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
            updates = await sender.client(EditChatPhotoRequest(chat_id=self.tgid, photo=photo))
        else:
            channel = await self.get_input_entity(sender)
            updates = await sender.client(EditPhotoRequest(channel=channel, photo=photo))
        for update in updates.updates:
            is_photo_update = (isinstance(update, UpdateNewMessage)
                               and isinstance(update.message, MessageService)
                               and isinstance(update.message.action, MessageActionChatEditPhoto))
            if is_photo_update:
                loc = self._get_largest_photo_size(update.message.action.photo).location
                self.photo_id = f"{loc.volume_id}-{loc.local_id}"
                self.save()
                break

    # endregion
    # region Telegram chat info updating

    async def _get_telegram_users_in_matrix_room(self):
        user_tgids = set()
        user_mxids = await self.main_intent.get_room_members(self.mxid, ("join", "invite"))
        for user in user_mxids:
            if user == self.az.intent.mxid:
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
            # TODO[waiting-for-bots] This won't happen when the bot is enabled
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
        self.save()

        levels = await self.main_intent.get_power_levels(self.mxid)
        levels = self._get_base_power_levels(levels, entity)
        already_saved = await self.handle_matrix_power_levels(source, levels["users"], {})
        if not already_saved:
            await self.main_intent.set_power_levels(self.mxid, levels)

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

    async def handle_telegram_photo(self, source, intent, media, relates_to=None):
        largest_size = self._get_largest_photo_size(media.photo)
        file = await source.client.download_file_bytes(largest_size.location)
        mime_type = magic.from_buffer(file, mime=True)
        uploaded = await intent.upload_file(file, mime_type)
        info = {
            "h": largest_size.h,
            "w": largest_size.w,
            "size": len(largest_size.bytes) if (
                isinstance(largest_size, PhotoCachedSize)) else largest_size.size,
            "orientation": 0,
            "mimetype": mime_type,
        }
        name = media.caption
        await intent.set_typing(self.mxid, is_typing=False)
        return await intent.send_image(self.mxid, uploaded["content_uri"], info=info,
                                       text=name, relates_to=relates_to)

    def convert_webp(self, file, to="png"):
        try:
            image = Image.open(BytesIO(file)).convert("RGBA")
            new_file = BytesIO()
            image.save(new_file, to)
            return f"image/{to}", new_file.getvalue()
        except Exception:
            self.log.exception(f"Failed to convert webp to {to}")
            return "image/webp", file

    async def handle_telegram_document(self, source, intent, media, relates_to=None):
        file = await source.client.download_file_bytes(media.document)
        mime_type = magic.from_buffer(file, mime=True)
        dont_change_mime = False
        if mime_type == "image/webp":
            mime_type, file = self.convert_webp(file, to="png")
            dont_change_mime = True
        uploaded = await intent.upload_file(file, mime_type)
        name = media.caption
        for attr in media.document.attributes:
            if not name and isinstance(attr, DocumentAttributeFilename):
                name = attr.file_name
                if not dont_change_mime:
                    (mime_from_name, _) = mimetypes.guess_type(name)
                    mime_type = mime_from_name or mime_type
            elif isinstance(attr, DocumentAttributeSticker):
                name = f"Sticker for {attr.alt}"
        mime_type = media.document.mime_type or mime_type
        info = {
            "size": media.document.size,
            "mimetype": mime_type,
        }
        type = "m.file"
        if mime_type.startswith("video/"):
            type = "m.video"
        elif mime_type.startswith("audio/"):
            type = "m.audio"
        elif mime_type.startswith("image/"):
            type = "m.image"
        await intent.set_typing(self.mxid, is_typing=False)
        return await intent.send_file(self.mxid, uploaded["content_uri"], info=info,
                                      text=name, file_type=type, relates_to=relates_to)

    def handle_telegram_location(self, source, intent, location, relates_to=None):
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
        })

    async def handle_telegram_text(self, source, intent, evt):
        self.log.debug(f"Sending {evt.message} to {self.mxid} by {intent.mxid}")
        text, html, relates_to = await formatter.telegram_event_to_matrix(
            evt, source,
            config["bridge.native_replies"],
            config["bridge.link_in_reply"],
            self.main_intent)
        await intent.set_typing(self.mxid, is_typing=False)
        return await intent.send_text(self.mxid, text, html=html, relates_to=relates_to)

    async def handle_telegram_edit(self, source, sender, evt):
        if not self.mxid:
            return
        elif not config["bridge.edits_as_replies"]:
            self.log.debug("Edits as replies disabled, ignoring edit event...")
            return

        tg_space = self.tgid if self.peer_type == "channel" else source.tgid
        temporary_identifier = f"${random.randint(1000000000000,9999999999999)}TGBRIDGEDITEMP"
        duplicate_found = self.is_duplicate(evt, (temporary_identifier, tg_space))
        if duplicate_found:
            mxid, other_tg_space = duplicate_found
            if tg_space != other_tg_space:
                msg = DBMessage.query.get((evt.id, tg_space))
                msg.mxid = mxid
                msg.mx_room = self.mxid
                self.db.commit()
            return

        evt.reply_to_msg_id = evt.id
        text, html, relates_to = await formatter.telegram_event_to_matrix(
            evt, source,
            config["bridge.native_replies"],
            config["bridge.link_in_reply"],
            self.main_intent, reply_text="Edit")
        intent = sender.intent if sender else self.main_intent
        await intent.set_typing(self.mxid, is_typing=False)
        response = await intent.send_text(self.mxid, text, html=html, relates_to=relates_to)

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

        intent = sender.intent if sender else self.main_intent
        if evt.message:
            response = await self.handle_telegram_text(source, intent, evt)
        elif evt.media:
            relates_to = formatter.telegram_reply_to_matrix(evt, source)
            if isinstance(evt.media, MessageMediaPhoto):
                response = await self.handle_telegram_photo(source, intent, evt.media, relates_to)
            elif isinstance(evt.media, MessageMediaDocument):
                response = await self.handle_telegram_document(source, intent, evt.media,
                                                               relates_to)
            elif isinstance(evt.media, MessageMediaGeo):
                response = await self.handle_telegram_location(source, intent, evt.media.geo,
                                                               relates_to)
            else:
                self.log.debug("Unhandled Telegram media: %s", evt.media)
                return
        else:
            self.log.debug("Unhandled Telegram message: %s", evt)
            return

        mxid = response["event_id"]
        DBMessage.query \
            .filter(DBMessage.mx_room == self.mxid,
                    DBMessage.mxid == temporary_identifier) \
            .update({"mxid": mxid})
        self.db.add(DBMessage(tgid=evt.id, mx_room=self.mxid, mxid=mxid, tg_space=tg_space))
        self.db.commit()

    async def handle_telegram_action(self, source, sender, action):
        if not self.mxid:
            create_and_exit = (MessageActionChatCreate, MessageActionChannelCreate)
            create_and_continue = (MessageActionChatAddUser, MessageActionChatJoinedByLink)
            if isinstance(action, create_and_exit + create_and_continue):
                await self.create_matrix_room(source, invites=[source.mxid],
                                              update_if_exists=isinstance(action, create_and_exit))
            if not isinstance(action, create_and_continue):
                return

        # TODO figure out how to see changes to about text / channel username
        if isinstance(action, MessageActionChatEditTitle):
            if await self.update_title(action.title):
                self.save()
        elif isinstance(action, MessageActionChatEditPhoto):
            largest_size = self._get_largest_photo_size(action.photo)
            if await self.update_avatar(source, largest_size.location):
                self.save()
        elif isinstance(action, MessageActionChatAddUser):
            for user_id in action.users:
                await self.add_telegram_user(user_id, source)
        elif isinstance(action, MessageActionChatJoinedByLink):
            await self.add_telegram_user(sender.id, source)
        elif isinstance(action, MessageActionChatDeleteUser):
            kick_message = (f"Kicked by {sender.displayname}"
                            if sender.id != action.user_id else None)
            await self.delete_telegram_user(action.user_id, kick_message)
        elif isinstance(action, MessageActionChatMigrateTo):
            self.peer_type = "channel"
            self.migrate_and_save(action.channel_id)
            await sender.intent.send_emote(self.mxid, "upgraded this group to a supergroup.")
        else:
            self.log.debug("Unhandled Telegram action in %s: %s", self.title, action)

    async def set_telegram_admin(self, puppet, user):
        levels = await self.main_intent.get_power_levels(self.mxid)
        if user:
            levels["users"][user.mxid] = 50
        if puppet:
            levels["users"][puppet.mxid] = 50
        await self.main_intent.set_power_levels(self.mxid, levels)

    async def update_telegram_pin(self, source, id):
        space = self.tgid if self.peer_type == "channel" else source.tgid
        message = DBMessage.query.get((id, space))
        if message:
            await self.main_intent.set_pinned_messages(self.mxid, [message.mxid])
        else:
            await self.main_intent.set_pinned_messages(self.mxid, [])

    def _participants_to_power_levels(self, participants, levels):
        changed = False
        admin_power_level = 75 if self.peer_type == "channel" else 50
        if levels["events"]["m.room.power_levels"] != admin_power_level:
            changed = True
            levels["events"]["m.room.power_levels"] = admin_power_level

        for participant in participants:
            puppet = p.Puppet.get(participant.user_id)
            user = u.User.get_by_tgid(participant.user_id)
            new_level = 0
            if isinstance(participant, (ChatParticipantAdmin, ChannelParticipantAdmin)):
                new_level = 50
            elif isinstance(participant, (ChatParticipantCreator, ChannelParticipantCreator)):
                new_level = 95

            user_levels = levels["users"]

            if user:
                user.register_portal(self)
                user_level_defined = user.mxid in user_levels
                user_has_right_level = (user_levels[user.mxid] == new_level
                                        if user_level_defined else new_level == 0)
                if not user_has_right_level:
                    levels["users"][user.mxid] = new_level
                    changed = True

            if puppet:
                puppet_level_defined = puppet.mxid in user_levels
                puppet_has_right_level = (user_levels[puppet.mxid] == new_level
                                          if puppet_level_defined else new_level == 0)

                if not puppet_has_right_level:
                    levels["users"][puppet.mxid] = new_level
                    changed = True
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

    def to_db(self, merge=True):
        portal = DBPortal(tgid=self.tgid, tg_receiver=self.tg_receiver, peer_type=self.peer_type,
                          mxid=self.mxid, username=self.username, title=self.title,
                          about=self.about, photo_id=self.photo_id)
        if merge:
            return self.db.merge(portal)
        return portal

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
        self.to_db()
        self.db.commit()

    def delete(self):
        self.db.delete(self.to_db())
        self.db.commit()

    @classmethod
    def from_db(cls, db_portal):
        return Portal(tgid=db_portal.tgid, tg_receiver=db_portal.tg_receiver,
                      peer_type=db_portal.peer_type, mxid=db_portal.mxid,
                      username=db_portal.username, title=db_portal.title,
                      about=db_portal.about, photo_id=db_portal.photo_id)

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
            portal = Portal(tgid, peer_type=peer_type, tg_receiver=tg_receiver,
                            save_to_cache=False)
            cls.db.add(portal.to_db(merge=False))
            cls.db.commit()
            cls.by_tgid[portal.tgid_full] = portal
            return portal

        return None

    @classmethod
    def get_by_entity(cls, entity, receiver_id=None):
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
        return cls.get_by_tgid(id, receiver_id if type_name == "user" else id, type_name)

    # endregion


def init(context):
    global config
    Portal.az, Portal.db, config, _ = context
