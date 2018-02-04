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
from telethon.tl.functions.messages import *
from telethon.tl.functions.channels import *
from telethon.errors.rpc_error_list import *
from telethon.tl.types import *
from PIL import Image
from io import BytesIO
from datetime import datetime
import mimetypes
import magic
from .db import Portal as DBPortal, Message as DBMessage
from . import puppet as p, user as u, formatter

mimetypes.init()

config = None


class Portal:
    log = None
    db = None
    az = None
    by_mxid = {}
    by_tgid = {}

    def __init__(self, tgid, peer_type, tg_receiver=None, mxid=None, username=None, title=None,
                 about=None, photo_id=None):
        self.mxid = mxid
        self.tgid = tgid
        self.tg_receiver = tg_receiver or tgid
        self.peer_type = peer_type
        self.username = username
        self.title = title
        self.about = about
        self.photo_id = photo_id
        self._main_intent = None

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

    def invite_matrix(self, users):
        if isinstance(users, str):
            self.main_intent.invite(self.mxid, users)
        elif isinstance(users, list):
            for user in users:
                self.main_intent.invite(self.mxid, user)
        else:
            raise ValueError("Invalid invite identifier given to invite_matrix()")

    def update_after_create(self, user, entity, direct, puppet=None):
        if not direct:
            self.update_info(user, entity)
            users, participants = self.get_users(user, entity)
            self.sync_telegram_users(user, users)
            self.update_telegram_participants(participants)
        else:
            if not puppet:
                puppet = p.Puppet.get(self.tgid)
            puppet.update_info(user, entity)
            puppet.intent.join_room(self.mxid)

    def create_matrix_room(self, user, entity=None, invites=None, update_if_exists=True):
        if not entity:
            entity = user.client.get_entity(self.peer)
            self.log.debug("Fetched data: %s", entity)
        direct = self.peer_type == "user"

        if self.mxid:
            if update_if_exists:
                self.update_after_create(user, entity, direct)
            self.invite_matrix(invites or [])
            return self.mxid

        self.log.debug(f"Creating room for {self.tgid_log}")

        try:
            self.title = entity.title
        except AttributeError:
            self.title = None

        puppet = p.Puppet.get(self.tgid) if direct else None
        intent = puppet.intent if direct else self.az.intent

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
            intent.remove_room_alias(alias)
        room = intent.create_room(alias=alias, is_public=public, invitees=invites or [],
                                  name=self.title, is_direct=direct)
        if not room:
            raise Exception(f"Failed to create room for {self.tgid_log}")

        self.mxid = room["room_id"]
        self.by_mxid[self.mxid] = self
        self.save()

        power_level_requirement = 0 if self.peer_type == "chat" and entity.admins_enabled else 50
        levels = self.main_intent.get_power_levels(self.mxid)
        levels["ban"] = 100
        levels["invite"] = 50
        levels["events"]["m.room.name"] = power_level_requirement
        levels["events"]["m.room.avatar"] = power_level_requirement
        levels["events"]["m.room.topic"] = 50 if self.peer_type == "channel" else 100
        levels["events"]["m.room.power_levels"] = 75
        self.main_intent.set_power_levels(self.mxid, levels)
        self.update_after_create(user, entity, direct, puppet)

    def _get_room_alias(self, username=None):
        username = username or self.username
        return config.get("bridge.alias_template", "telegram_{groupname}").format(
            groupname=username)

    def sync_telegram_users(self, source, users):
        for entity in users:
            puppet = p.Puppet.get(entity.id)
            puppet.update_info(source, entity)
            puppet.intent.ensure_joined(self.mxid)

    def add_telegram_user(self, user_id, source=None):
        puppet = p.Puppet.get(user_id)
        if source:
            entity = source.client.get_entity(user_id)
            puppet.update_info(source, entity)
        puppet.intent.join_room(self.mxid)

        user = u.User.get_by_tgid(user_id)
        if user:
            self.main_intent.invite(self.mxid, user.mxid)

    def delete_telegram_user(self, user_id, kick_message=None):
        puppet = p.Puppet.get(user_id)
        user = u.User.get_by_tgid(user_id)
        if kick_message:
            self.main_intent.kick(self.mxid, puppet.mxid, kick_message)
        else:
            puppet.intent.leave_room(self.mxid)
        if user:
            self.main_intent.kick(self.mxid, user.mxid, kick_message or "Left Telegram chat")

    def update_info(self, user, entity=None):
        if self.peer_type == "user":
            self.log.warn(f"Called update_info() for direct chat portal {self.tgid_log}")
            return

        self.log.debug(f"Updating info of {self.tgid_log}")
        if not entity:
            entity = user.client.get_entity(self.peer)
            self.log.debug("Fetched data: %s", entity)
        changed = False

        if self.peer_type == "channel":
            changed = self.update_username(entity.username) or changed
            # TODO update about text
            # changed = self.update_about(entity.about) or changed

        changed = self.update_title(entity.title) or changed

        if isinstance(entity.photo, ChatPhoto):
            changed = self.update_avatar(user, entity.photo.photo_big) or changed

        if changed:
            self.save()

    def update_username(self, username):
        if self.username != username:
            if self.username:
                self.main_intent.remove_room_alias(self._get_room_alias())
            self.username = username
            if self.username:
                self.main_intent.add_room_alias(self.mxid, self._get_room_alias())
            return True
        return False

    def update_about(self, about):
        if self.about != about:
            self.about = about
            self.main_intent.set_room_topic(self.mxid, self.about)
            return True
        return False

    def update_title(self, title):
        if self.title != title:
            self.title = title
            self.main_intent.set_room_name(self.mxid, self.title)
            return True
        return False

    @staticmethod
    def _get_largest_photo_size(photo):
        return max(photo.sizes, key=(lambda photo2: (
            len(photo2.bytes) if isinstance(photo2, PhotoCachedSize) else photo2.size)))

    def update_avatar(self, user, photo):
        photo_id = f"{photo.volume_id}-{photo.local_id}"
        if self.photo_id != photo_id:
            try:
                file = user.download_file(photo)
            except LocationInvalidError:
                return False
            uploaded = self.main_intent.upload_file(file)
            self.main_intent.set_room_avatar(self.mxid, uploaded["content_uri"])
            self.photo_id = photo_id
            return True
        return False

    def get_users(self, user, entity):
        if self.peer_type == "chat":
            chat = user.client(GetFullChatRequest(chat_id=self.tgid))
            return chat.users, chat.full_chat.participants.participants
        elif self.peer_type == "channel":
            try:
                participants = user.client(GetParticipantsRequest(
                    entity, ChannelParticipantsRecent(), offset=0, limit=100, hash=0
                ))
                return participants.users, participants.participants
            except ChatAdminRequiredError:
                return [], []
        elif self.peer_type == "user":
            return [entity], []

    def get_invite_link(self, user):
        if self.peer_type == "user":
            raise ValueError("You can't invite users to private chats.")
        elif self.peer_type == "chat":
            link = user.client(ExportChatInviteRequest(chat_id=self.tgid))
        elif self.peer_type == "channel":
            link = user.client(
                ExportInviteRequest(channel=self.get_input_entity(user)))
        else:
            raise ValueError(f"Invalid peer type '{self.peer_type}' for invite link.")

        if isinstance(link, ChatInviteEmpty):
            raise ValueError("Failed to get invite link.")

        return link.link

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

    def leave_matrix(self, user, source):
        if self.peer_type == "user":
            self.main_intent.leave_room(self.mxid)
            self.delete()
            del self.by_tgid[self.tgid_full]
            del self.by_mxid[self.mxid]
        elif source and source.tgid != user.tgid:
            target = user.get_input_entity(source)
            if self.peer_type == "chat":
                source.client(DeleteChatUserRequest(chat_id=self.tgid, user_id=target))
            else:
                channel = self.get_input_entity(source)
                rights = ChannelBannedRights(datetime.fromtimestamp(0), True)
                source.client(EditBannedRequest(channel=channel,
                                                user_id=target,
                                                banned_rights=rights))
        elif self.peer_type == "chat":
            user.client(DeleteChatUserRequest(chat_id=self.tgid, user_id=InputUserSelf()))
        elif self.peer_type == "channel":
            channel = self.get_input_entity(user)
            user.client(LeaveChannelRequest(channel=channel))

    def handle_matrix_message(self, sender, message, event_id):
        type = message["msgtype"]
        if type in {"m.text", "m.emote"}:
            if "format" in message and message["format"] == "org.matrix.custom.html":
                message, entities = formatter.matrix_to_telegram(message["formatted_body"],
                                                                 sender.tgid)
                if type == "m.emote":
                    message = "/me " + message
                reply_to = None
                if len(entities) > 0 and isinstance(entities[0], formatter.MessageEntityReply):
                    reply_to = entities.pop(0).msg_id
                response = sender.send_message(self.peer, message, entities=entities,
                                               reply_to=reply_to)
            else:
                if type == "m.emote":
                    message["body"] = "/me " + message["body"]
                response = sender.send_message(self.peer, message["body"])
        elif type in {"m.image", "m.file", "m.audio", "m.video"}:
            file = self.main_intent.download_file(message["url"])

            info = message["info"]
            mime = info["mimetype"]

            file_name, caption = self._get_file_meta(message["body"], mime)

            attributes = [DocumentAttributeFilename(file_name=file_name)]
            if "w" in info and "h" in info:
                attributes.append(DocumentAttributeImageSize(w=info["w"], h=info["h"]))

            response = sender.send_file(self.peer, file, mime, caption, attributes, file_name)
        else:
            self.log.debug("Unhandled Matrix event: %s", message)
            return
        self.db.add(
            DBMessage(tgid=response.id, mx_room=self.mxid, mxid=event_id, user=sender.tgid))
        self.db.commit()

    def handle_matrix_deletion(self, deleter, event_id):
        message = DBMessage.query.filter(DBMessage.mxid == event_id and
                                         DBMessage.user == deleter.tgid and
                                         DBMessage.mx_room == self.mxid).one_or_none()
        if not message:
            return
        deleter.client.delete_messages(self.peer, [message.tgid])

    def handle_matrix_power_levels(self, sender, new_users, old_users):
        # TODO handle all power level changes and bridge exact admin rights to supergroups/channels
        for user, level in new_users.items():
            user_id = p.Puppet.get_id_from_mxid(user)
            if not user_id:
                mx_user = u.User.get_by_mxid(user, create=False)
                if not mx_user or not mx_user.tgid:
                    continue
                user_id = mx_user.tgid
            if user not in old_users or level != old_users[user]:
                if self.peer_type == "chat":
                    sender.client(EditChatAdminRequest(
                        chat_id=self.tgid, user_id=user_id, is_admin=level >= 50))
                elif self.peer_type == "channel":
                    moderator = level >= 50
                    admin = level >= 75
                    rights = ChannelAdminRights(change_info=moderator, post_messages=moderator,
                                                edit_messages=moderator, delete_messages=moderator,
                                                ban_users=moderator, invite_users=moderator,
                                                invite_link=moderator, pin_messages=moderator,
                                                add_admins=admin, manage_call=moderator)
                    sender.client(
                        EditAdminRequest(channel=self.get_input_entity(sender),
                                         user_id=sender.client.get_input_entity(PeerUser(user_id)),
                                         admin_rights=rights))

    def handle_matrix_about(self, sender, about):
        if self.peer_type not in {"channel"}:
            return
        channel = self.get_input_entity(sender)
        sender.client(EditAboutRequest(channel=channel, about=about))
        self.about = about
        self.save()

    def handle_matrix_title(self, sender, title):
        if self.peer_type not in {"chat", "channel"}:
            return

        if self.peer_type == "chat":
            sender.client(EditChatTitleRequest(chat_id=self.tgid, title=title))
        else:
            channel = self.get_input_entity(sender)
            sender.client(EditTitleRequest(channel=channel, title=title))
        self.title = title
        self.save()

    def handle_matrix_avatar(self, sender, url):
        if self.peer_type not in {"chat", "channel"}:
            # Invalid peer type
            return

        file = self.main_intent.download_file(url)
        mime = magic.from_buffer(file, mime=True)
        ext = mimetypes.guess_extension(mime)
        uploaded = sender.client.upload_file(file, file_name=f"avatar{ext}")
        photo = InputChatUploadedPhoto(file=uploaded)

        if self.peer_type == "chat":
            updates = sender.client(EditChatPhotoRequest(chat_id=self.tgid, photo=photo))
        else:
            channel = self.get_input_entity(sender)
            updates = sender.client(EditPhotoRequest(channel=channel, photo=photo))
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

    def _get_telegram_users_in_matrix_room(self):
        user_tgids = set()
        user_mxids = self.main_intent.get_room_members(self.mxid, ("join", "invite"))
        for user in user_mxids:
            if user == self.az.intent.mxid:
                continue
            mx_user = u.User.get_by_mxid(user, create=False)
            if mx_user and mx_user.tgid:
                user_tgids.add(mx_user.tgid)
            puppet_id = p.Puppet.get_id_from_mxid(user)
            if puppet_id:
                user_tgids.add(puppet_id)
        return user_tgids

    def upgrade_telegram_chat(self, source):
        if self.peer_type != "chat":
            raise ValueError("Only normal group chats are upgradable to supergroups.")

        updates = source.client(MigrateChatRequest(chat_id=self.tgid))
        entity = None
        for chat in updates.chats:
            if isinstance(chat, Channel):
                entity = chat
                break
        if not entity:
            raise ValueError("Upgrade may have failed: output channel not found.")
        self.peer_type = "channel"
        self.migrate_and_save(entity.id)
        self.update_info(source, entity)

    def create_telegram_chat(self, source, supergroup=False):
        if not self.mxid:
            raise ValueError("Can't create Telegram chat for portal without Matrix room.")
        elif self.tgid:
            raise ValueError("Can't create Telegram chat for portal with existing Telegram chat.")

        invites = self._get_telegram_users_in_matrix_room()
        if len(invites) < 2:
            # TODO[waiting-for-bots] This won't happen when the bot is enabled
            raise ValueError("Not enough Telegram users to create a chat")

        invites = [source.client.get_input_entity(id) for id in invites]

        if self.peer_type == "chat":
            updates = source.client(CreateChatRequest(title=self.title, users=invites))
            entity = updates.chats[0]
        elif self.peer_type == "channel":
            updates = source.client(CreateChannelRequest(title=self.title, about=self.about or "",
                                                         megagroup=supergroup))
            entity = updates.chats[0]
            source.client(InviteToChannelRequest(channel=source.client.get_input_entity(entity),
                                                 users=invites))
        else:
            raise ValueError("Invalid peer type for Telegram chat creation")

        self.tgid = entity.id
        self.tg_receiver = self.tgid
        self.by_tgid[self.tgid_full] = self
        self.update_info(source, entity)
        self.save()

    def invite_telegram(self, source, puppet):
        if self.peer_type == "chat":
            source.client(AddChatUserRequest(chat_id=self.tgid, user_id=puppet.tgid, fwd_limit=0))
        elif self.peer_type == "channel":
            target = puppet.get_input_entity(source)
            source.client(InviteToChannelRequest(channel=self.peer, users=[target]))
        else:
            raise ValueError("Invalid peer type for Telegram user invite")

    # endregion
    # region Telegram event handling

    def handle_telegram_typing(self, user, event):
        if self.mxid:
            user.intent.set_typing(self.mxid, is_typing=True)

    def handle_telegram_photo(self, source, sender, media):
        largest_size = self._get_largest_photo_size(media.photo)
        file = source.download_file(largest_size.location)
        mime_type = magic.from_buffer(file, mime=True)
        uploaded = sender.intent.upload_file(file, mime_type)
        info = {
            "h": largest_size.h,
            "w": largest_size.w,
            "size": len(largest_size.bytes) if (
                isinstance(largest_size, PhotoCachedSize)) else largest_size.size,
            "orientation": 0,
            "mimetype": mime_type,
        }
        name = media.caption
        sender.intent.set_typing(self.mxid, is_typing=False)
        return sender.intent.send_image(self.mxid, uploaded["content_uri"], info=info, text=name)

    def convert_webp(self, file, to="png"):
        try:
            image = Image.open(BytesIO(file)).convert("RGBA")
            new_file = BytesIO()
            image.save(new_file, to)
            return f"image/{to}", new_file.getvalue()
        except Exception:
            self.log.exception(f"Failed to convert webp to {to}")
            return "image/webp", file

    def handle_telegram_document(self, source, sender, media):
        file = source.download_file(media.document)
        mime_type = magic.from_buffer(file, mime=True)
        dont_change_mime = False
        if mime_type == "image/webp":
            mime_type, file = self.convert_webp(file, to="png")
            dont_change_mime = True
        uploaded = sender.intent.upload_file(file, mime_type)
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
        sender.intent.set_typing(self.mxid, is_typing=False)
        return sender.intent.send_file(self.mxid, uploaded["content_uri"], info=info, text=name,
                                       file_type=type)

    def handle_telegram_location(self, source, sender, location):
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

        return sender.intent.send_message(self.mxid, {
            "msgtype": "m.location",
            "geo_uri": f"geo:{lat},{long}",
            "body": body,
            "format": "org.matrix.custom.html",
            "formatted_body": formatted_body,
        })

    def handle_telegram_text(self, source, sender, evt):
        self.log.debug(f"Sending {evt.message} to {self.mxid} by {sender.id}")
        text, html = formatter.telegram_event_to_matrix(evt, source)
        sender.intent.set_typing(self.mxid, is_typing=False)
        return sender.intent.send_text(self.mxid, text, html=html)

    def handle_telegram_message(self, source, sender, evt):
        if not self.mxid:
            self.create_matrix_room(source, invites=[source.mxid])

        if evt.message:
            response = self.handle_telegram_text(source, sender, evt)
        elif evt.media:
            if isinstance(evt.media, MessageMediaPhoto):
                response = self.handle_telegram_photo(source, sender, evt.media)
            elif isinstance(evt.media, MessageMediaDocument):
                response = self.handle_telegram_document(source, sender, evt.media)
            elif isinstance(evt.media, MessageMediaGeo):
                response = self.handle_telegram_location(source, sender, evt.media.geo)
            else:
                self.log.debug("Unhandled Telegram media: %s", evt.media)
                return
        else:
            self.log.debug("Unhandled Telegram message: %s", evt)
            return

        self.db.add(DBMessage(tgid=evt.id, mx_room=self.mxid, mxid=response["event_id"],
                              user=source.tgid))
        self.db.commit()

    def handle_telegram_action(self, source, sender, action):
        if not self.mxid:
            create_and_exit = (MessageActionChatCreate, MessageActionChannelCreate)
            create_and_continue = (MessageActionChatAddUser, MessageActionChatJoinedByLink)
            if isinstance(action, create_and_exit + create_and_continue):
                self.create_matrix_room(source, invites=[source.mxid])
            if not isinstance(action, create_and_continue):
                return

        # TODO figure out how to see changes to about text / channel username
        if isinstance(action, MessageActionChatEditTitle):
            if self.update_title(action.title):
                self.save()
        elif isinstance(action, MessageActionChatEditPhoto):
            largest_size = self._get_largest_photo_size(action.photo)
            if self.update_avatar(source, largest_size.location):
                self.save()
        elif isinstance(action, MessageActionChatAddUser):
            for user_id in action.users:
                self.add_telegram_user(user_id, source)
        elif isinstance(action, MessageActionChatJoinedByLink):
            self.add_telegram_user(sender.id, source)
        elif isinstance(action, MessageActionChatDeleteUser):
            kick_message = None
            if sender.id != action.user_id:
                kick_message = f"Kicked by {sender.displayname}"
            self.delete_telegram_user(action.user_id, kick_message)
        elif isinstance(action, MessageActionChatMigrateTo):
            self.peer_type = "channel"
            self.migrate_and_save(action.channel_id)
            sender.intent.send_emote(self.mxid, "upgraded this group to a supergroup.")
        else:
            self.log.debug("Unhandled Telegram action in %s: %s", self.title, action)

    def set_telegram_admin(self, puppet, user):
        levels = self.main_intent.get_power_levels(self.mxid)
        if user:
            levels["users"][user.mxid] = 50
        if puppet:
            levels["users"][puppet.mxid] = 50
        self.main_intent.set_power_levels(self.mxid, levels)

    def update_telegram_pin(self, source, id):
        message = DBMessage.query.get((id, source.tgid))
        if message:
            self.main_intent.set_pinned_messages(self.mxid, [message.mxid])
        else:
            self.main_intent.set_pinned_messages(self.mxid, [])

    def update_telegram_participants(self, participants):
        levels = self.main_intent.get_power_levels(self.mxid)
        changed = False

        admin_power_level = 75 if self.peer_type == "channel" else 50
        if levels["events"]["m.room.power_levels"] != admin_power_level:
            changed = True
            levels["events"]["m.room.power_levels"] = admin_power_level

        for participant in participants:
            puppet = p.Puppet.get(participant.user_id)
            user = u.User.get_by_tgid(participant.user_id)
            print(participant)
            new_level = 0
            if isinstance(participant, (ChatParticipantAdmin, ChannelParticipantAdmin)):
                new_level = 50
            elif isinstance(participant, (ChatParticipantCreator, ChannelParticipantCreator)):
                new_level = 95
            if user and (user.mxid in levels["users"] or new_level > 0):
                levels["users"][user.mxid] = new_level
                changed = True
            if puppet and (puppet.mxid in levels["users"] or new_level > 0):
                levels["users"][puppet.mxid] = new_level
                changed = True
        if changed:
            self.main_intent.set_power_levels(self.mxid, levels)

    def set_telegram_admins_enabled(self, enabled):
        level = 50 if enabled else 10
        levels = self.main_intent.get_power_levels(self.mxid)
        levels["invite"] = level
        levels["events"]["m.room.name"] = level
        levels["events"]["m.room.avatar"] = level
        self.main_intent.set_power_levels(self.mxid, levels)

    # endregion
    # region Database conversion

    def to_db(self):
        return self.db.merge(
            DBPortal(tgid=self.tgid, tg_receiver=self.tg_receiver, peer_type=self.peer_type,
                     mxid=self.mxid, username=self.username, title=self.title,
                     about=self.about, photo_id=self.photo_id))

    def migrate_and_save(self, new_id):
        existing = DBPortal.query.get(self.tgid_full)
        if existing:
            self.db.object_session(existing).delete(existing)
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
            portal = Portal(tgid, peer_type=peer_type, tg_receiver=tg_receiver)
            cls.db.add(portal.to_db())
            portal.save()
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
    Portal.az, Portal.db, log, config = context
    Portal.log = log.getChild("portal")
