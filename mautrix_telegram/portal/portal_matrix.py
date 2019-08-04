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
from typing import Awaitable, Dict, List, Optional, Tuple, Union, Any, TYPE_CHECKING
from html import escape as escape_html
from string import Template
import mimetypes

import magic

from telethon.tl.functions.messages import (
    DeleteChatUserRequest, EditChatAdminRequest, EditChatPhotoRequest, EditChatTitleRequest,
    UpdatePinnedMessageRequest, SetTypingRequest, EditChatAboutRequest)
from telethon.tl.functions.channels import (
    EditAdminRequest, EditPhotoRequest, EditTitleRequest, JoinChannelRequest, LeaveChannelRequest)
from telethon.tl.functions.messages import ReadHistoryRequest as ReadMessageHistoryRequest
from telethon.tl.functions.channels import ReadHistoryRequest as ReadChannelHistoryRequest
from telethon.errors import (ChatNotModifiedError, PhotoExtInvalidError,
                             PhotoInvalidDimensionsError, PhotoSaveFileInvalidError)
from telethon.tl.patched import Message, MessageService
from telethon.tl.types import (
    ChatAdminRights, DocumentAttributeFilename, DocumentAttributeImageSize, GeoPoint,
    InputChatUploadedPhoto, InputUserSelf, MessageActionChatEditPhoto, MessageMediaGeo,
    SendMessageCancelAction, SendMessageTypingAction, TypeInputPeer, TypeMessageEntity,
    UpdateNewMessage, InputMediaUploadedDocument)

from mautrix.types import (EventID, RoomID, UserID, ContentURI, MessageType,
                           TextMessageEventContent, Format)
from mautrix.bridge import BasePortal as AbstractPortal

from ..types import TelegramID
from ..db import Message as DBMessage
from ..util import sane_mimetypes
from .. import puppet as p, user as u, formatter, util
from .base import BasePortal

if TYPE_CHECKING:
    from ..abstract_user import AbstractUser
    from ..tgclient import MautrixTelegramClient

TypeMessage = Union[Message, MessageService]


class PortalMatrix(BasePortal, AbstractPortal):
    @staticmethod
    def _get_file_meta(body: str, mime: str) -> str:
        try:
            current_extension = body[body.rindex("."):].lower()
            body = body[:body.rindex(".")]
            if mimetypes.types_map[current_extension] == mime:
                return body + current_extension
        except (ValueError, KeyError):
            pass
        if mime:
            return f"matrix_upload{sane_mimetypes.guess_extension(mime)}"
        return ""

    async def _get_state_change_message(self, event: str, user: 'u.User', **kwargs: Any
                                        ) -> Optional[str]:
        tpl = self.get_config(f"state_event_formats.{event}")
        if len(tpl) == 0:
            # Empty format means they don't want the message
            return None
        displayname = await self.get_displayname(user)

        tpl_args = {
            "mxid": user.mxid,
            "username": user.mxid_localpart,
            "displayname": escape_html(displayname),
            **kwargs,
        }
        return Template(tpl).safe_substitute(tpl_args)

    async def _send_state_change_message(self, event: str, user: 'u.User', event_id: EventID,
                                         **kwargs: Any) -> None:
        if not self.has_bot:
            return
        async with self.send_lock(self.bot.tgid):
            message = await self._get_state_change_message(event, user, **kwargs)
            if not message:
                return
            response = await self.bot.client.send_message(
                self.peer, message,
                parse_mode=self._matrix_event_to_entities)
            space = self.tgid if self.peer_type == "channel" else self.bot.tgid
            self.dedup.check(response, (event_id, space))

    async def name_change_matrix(self, user: 'u.User', displayname: str, prev_displayname: str,
                                 event_id: EventID) -> None:
        await self._send_state_change_message("name_change", user, event_id,
                                              displayname=displayname,
                                              prev_displayname=prev_displayname)

    async def get_displayname(self, user: 'u.User') -> str:
        # FIXME this doesn't seem to support per-room names or use cache in mautrix 0.4
        return (await self.main_intent.get_displayname(self.mxid, user.mxid)
                or user.mxid)

    def set_typing(self, user: 'u.User', typing: bool = True,
                   action: type = SendMessageTypingAction) -> Awaitable[bool]:
        return user.client(SetTypingRequest(
            self.peer, action() if typing else SendMessageCancelAction()))

    async def mark_read(self, user: 'u.User', event_id: EventID) -> None:
        if user.is_bot:
            return
        space = self.tgid if self.peer_type == "channel" else user.tgid
        message = DBMessage.get_by_mxid(event_id, self.mxid, space)
        if not message:
            return
        if self.peer_type == "channel":
            await user.client(ReadChannelHistoryRequest(
                channel=await self.get_input_entity(user), max_id=message.tgid))
        else:
            await user.client(ReadMessageHistoryRequest(peer=self.peer, max_id=message.tgid))

    async def kick_matrix(self, user: Union['u.User', 'p.Puppet'], source: 'u.User',
                          ban: bool = False) -> None:
        if user.tgid == source.tgid:
            return
        if isinstance(user, u.User) and await user.needs_relaybot(self):
            if not self.bot:
                return
            # TODO kick and ban message
            return
        if await source.needs_relaybot(self):
            if not self.has_bot:
                return
            source = self.bot
        target = await user.get_input_entity(source)
        if self.peer_type == "chat":
            await source.client(DeleteChatUserRequest(chat_id=self.tgid, user_id=target))
        elif self.peer_type == "channel":
            channel = await self.get_input_entity(source)
            await source.client.edit_permissions(channel, target, view_messages=False)
            if not ban:
                await source.client.edit_permissions(channel, target, view_messages=True)

    async def leave_matrix(self, user: 'u.User', event_id: EventID) -> None:
        if await user.needs_relaybot(self):
            await self._send_state_change_message("leave", user, event_id)
            return

        if self.peer_type == "user":
            await self.main_intent.leave_room(self.mxid)
            self.delete()
            try:
                del self.by_tgid[self.tgid_full]
                del self.by_mxid[self.mxid]
            except KeyError:
                pass
        elif self.peer_type == "chat":
            await user.client(DeleteChatUserRequest(chat_id=self.tgid, user_id=InputUserSelf()))
        elif self.peer_type == "channel":
            channel = await self.get_input_entity(user)
            await user.client(LeaveChannelRequest(channel=channel))

    async def join_matrix(self, user: 'u.User', event_id: EventID) -> None:
        if await user.needs_relaybot(self):
            await self._send_state_change_message("join", user, event_id)
            return

        if self.peer_type == "channel" and not user.is_bot:
            await user.client(JoinChannelRequest(channel=await self.get_input_entity(user)))
        else:
            # We'll just assume the user is already in the chat.
            pass

    async def _apply_msg_format(self, sender: 'u.User', msgtype: str, message: Dict[str, Any]
                                ) -> None:
        if "formatted_body" not in message:
            message["format"] = "org.matrix.custom.html"
            message["formatted_body"] = escape_html(message.get("body", "")).replace("\n", "<br/>")
        body = message["formatted_body"]

        tpl = (self.get_config(f"message_formats.[{msgtype}]")
               or "<b>$sender_displayname</b>: $message")
        displayname = await self.get_displayname(sender)
        tpl_args = dict(sender_mxid=sender.mxid,
                        sender_username=sender.mxid_localpart,
                        sender_displayname=escape_html(displayname),
                        message=body)
        message["formatted_body"] = Template(tpl).safe_substitute(tpl_args)

    async def _pre_process_matrix_message(self, sender: 'u.User', use_relaybot: bool,
                                          message: Dict[str, Any]) -> None:
        msgtype = message.get("msgtype", "m.text")
        if msgtype == "m.emote":
            await self._apply_msg_format(sender, msgtype, message)
            if "m.new_content" in message:
                await self._apply_msg_format(sender, msgtype, message["m.new_content"])
                message["m.new_content"]["msgtype"] = "m.text"
            message["msgtype"] = "m.text"
        elif use_relaybot:
            await self._apply_msg_format(sender, msgtype, message)
            if "m.new_content" in message:
                await self._apply_msg_format(sender, msgtype, message["m.new_content"])

    @staticmethod
    def _matrix_event_to_entities(event: Union[str, TextMessageEventContent]
                                  ) -> Tuple[str, Optional[List[TypeMessageEntity]]]:
        try:
            if isinstance(event, str):
                message, entities = formatter.matrix_to_telegram(event)
            elif isinstance(event, TextMessageEventContent) and event.format == Format.HTML:
                message, entities = formatter.matrix_to_telegram(event.formatted_body)
            else:
                message, entities = formatter.matrix_text_to_telegram(event.body)
        except KeyError:
            message, entities = None, None
        return message, entities

    async def _handle_matrix_text(self, sender_id: TelegramID, event_id: EventID,
                                  space: TelegramID, client: 'MautrixTelegramClient',
                                  message: Dict, reply_to: TelegramID) -> None:
        async with self.send_lock(sender_id):
            lp = self.get_config("telegram_link_preview")
            relates_to = message.get("m.relates_to", None) or {}
            if relates_to.get("rel_type", None) == "m.replace":
                orig_msg = DBMessage.get_by_mxid(relates_to.get("event_id", ""), self.mxid, space)
                if orig_msg and "m.new_content" in message:
                    message = message["m.new_content"]
                    response = await client.edit_message(self.peer, orig_msg.tgid, message,
                                                         parse_mode=self._matrix_event_to_entities,
                                                         link_preview=lp)
                    self._add_telegram_message_to_db(event_id, space, -1, response)
                    return
            response = await client.send_message(self.peer, message, reply_to=reply_to,
                                                 parse_mode=self._matrix_event_to_entities,
                                                 link_preview=lp)
            self._add_telegram_message_to_db(event_id, space, 0, response)

    async def _handle_matrix_file(self, msgtype: MessageType, sender_id: TelegramID,
                                  event_id: EventID, space: TelegramID,
                                  client: 'MautrixTelegramClient', message: dict,
                                  reply_to: TelegramID) -> None:
        file = await self.main_intent.download_media(message["url"])

        info = message.get("info", {})
        mime = info.get("mimetype", None)

        w, h = None, None

        if msgtype == MessageType.STICKER:
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

        caption = message["body"] if message["body"].lower() != file_name.lower() else None

        media = await client.upload_file_direct(
            file, mime, attributes, file_name,
            max_image_size=config["bridge.image_as_file_size"] * 1000 ** 2)
        async with self.send_lock(sender_id):
            if await self._matrix_document_edit(client, message, space, caption, media, event_id):
                return
            try:
                response = await client.send_media(self.peer, media, reply_to=reply_to,
                                                   caption=caption)
            except (PhotoInvalidDimensionsError, PhotoSaveFileInvalidError, PhotoExtInvalidError):
                media = InputMediaUploadedDocument(file=media.file, mime_type=mime,
                                                   attributes=attributes)
                response = await client.send_media(self.peer, media, reply_to=reply_to,
                                                   caption=caption)
            self._add_telegram_message_to_db(event_id, space, 0, response)

    async def _matrix_document_edit(self, client: 'MautrixTelegramClient', message: dict,
                                    space: TelegramID, caption: str, media: Any, event_id: EventID
                                    ) -> bool:
        relates_to = message.get("m.relates_to", None) or {}
        if relates_to.get("rel_type", None) == "m.replace":
            orig_msg = DBMessage.get_by_mxid(relates_to.get("event_id", ""), self.mxid, space)
            if orig_msg:
                response = await client.edit_message(self.peer, orig_msg.tgid,
                                                     caption, file=media)
                self._add_telegram_message_to_db(event_id, space, -1, response)
                return True
        return False

    async def _handle_matrix_location(self, sender_id: TelegramID, event_id: EventID,
                                      space: TelegramID, client: 'MautrixTelegramClient',
                                      message: Dict[str, Any], reply_to: TelegramID) -> None:
        try:
            lat, long = message["geo_uri"][len("geo:"):].split(",")
            lat, long = float(lat), float(long)
        except (KeyError, ValueError):
            self.log.exception("Failed to parse location")
            return None
        caption, entities = self._matrix_event_to_entities(message)
        media = MessageMediaGeo(geo=GeoPoint(lat, long, access_hash=0))

        async with self.send_lock(sender_id):
            if await self._matrix_document_edit(client, message, space, caption, media, event_id):
                return
            response = await client.send_media(self.peer, media, reply_to=reply_to,
                                               caption=caption, entities=entities)
            self._add_telegram_message_to_db(event_id, space, 0, response)

    def _add_telegram_message_to_db(self, event_id: EventID, space: TelegramID,
                                    edit_index: int, response: TypeMessage) -> None:
        self.log.debug("Handled Matrix message: %s", response)
        self.dedup.check(response, (event_id, space), force_hash=edit_index != 0)
        if edit_index < 0:
            prev_edit = DBMessage.get_one_by_tgid(TelegramID(response.id), space, -1)
            edit_index = prev_edit.edit_index + 1
        DBMessage(
            tgid=TelegramID(response.id),
            tg_space=space,
            mx_room=self.mxid,
            mxid=event_id,
            edit_index=edit_index).insert()

    async def handle_matrix_message(self, sender: 'u.User', message: Dict[str, Any],
                                    event_id: EventID) -> None:
        if "body" not in message or "msgtype" not in message:
            self.log.debug(f"Ignoring message {event_id} in {self.mxid} without body or msgtype")
            return

        puppet = p.Puppet.get_by_custom_mxid(sender.mxid)
        if puppet and message.get("net.maunium.telegram.puppet", False):
            self.log.debug("Ignoring puppet-sent message by confirmed puppet user %s", sender.mxid)
            return

        logged_in = not await sender.needs_relaybot(self)
        client = sender.client if logged_in else self.bot.client
        sender_id = sender.tgid if logged_in else self.bot.tgid
        space = (self.tgid if self.peer_type == "channel"  # Channels have their own ID space
                 else (sender.tgid if logged_in else self.bot.tgid))
        reply_to = formatter.matrix_reply_to_telegram(message, space, room_id=self.mxid)

        message["mxtg_filename"] = message["body"]
        await self._pre_process_matrix_message(sender, not logged_in, message)
        msgtype = message["msgtype"]

        if msgtype == "m.notice":
            bridge_notices = self.get_config("bridge_notices.default")
            excepted = sender.mxid in self.get_config("bridge_notices.exceptions")
            if not bridge_notices and not excepted:
                return

        if msgtype == "m.text" or msgtype == "m.notice":
            await self._handle_matrix_text(sender_id, event_id, space, client, message, reply_to)
        elif msgtype == "m.location":
            await self._handle_matrix_location(sender_id, event_id, space, client, message,
                                               reply_to)
        elif msgtype in ("m.sticker", "m.image", "m.file", "m.audio", "m.video"):
            await self._handle_matrix_file(msgtype, sender_id, event_id, space, client, message,
                                           reply_to)
        else:
            self.log.debug(f"Unhandled Matrix event: {message}")

    async def handle_matrix_pin(self, sender: 'u.User',
                                pinned_message: Optional[EventID]) -> None:
        if self.peer_type != "chat" and self.peer_type != "channel":
            return
        try:
            if not pinned_message:
                await sender.client(UpdatePinnedMessageRequest(peer=self.peer, id=0))
            else:
                tg_space = self.tgid if self.peer_type == "channel" else sender.tgid
                message = DBMessage.get_by_mxid(pinned_message, self.mxid, tg_space)
                if message is None:
                    self.log.warning(f"Could not find pinned {pinned_message} in {self.mxid}")
                    return
                await sender.client(UpdatePinnedMessageRequest(peer=self.peer, id=message.tgid))
        except ChatNotModifiedError:
            pass

    async def handle_matrix_deletion(self, deleter: 'u.User', event_id: EventID) -> None:
        real_deleter = deleter if not await deleter.needs_relaybot(self) else self.bot
        space = self.tgid if self.peer_type == "channel" else real_deleter.tgid
        message = DBMessage.get_by_mxid(event_id, self.mxid, space)
        if not message:
            return
        if message.edit_index == 0:
            await real_deleter.client.delete_messages(self.peer, [message.tgid])
        else:
            self.log.debug(f"Ignoring deletion of edit event {message.mxid} in {message.mx_room}")

    async def _update_telegram_power_level(self, sender: 'u.User', user_id: TelegramID,
                                           level: int) -> None:
        if self.peer_type == "chat":
            await sender.client(EditChatAdminRequest(
                chat_id=self.tgid, user_id=user_id, is_admin=level >= 50))
        elif self.peer_type == "channel":
            moderator = level >= 50
            admin = level >= 75
            rights = ChatAdminRights(change_info=moderator, post_messages=moderator,
                                     edit_messages=moderator, delete_messages=moderator,
                                     ban_users=moderator, invite_users=moderator,
                                     pin_messages=moderator, add_admins=admin)
            await sender.client(
                EditAdminRequest(channel=await self.get_input_entity(sender),
                                 user_id=user_id, admin_rights=rights))

    async def handle_matrix_power_levels(self, sender: 'u.User',
                                         new_users: Dict[UserID, int],
                                         old_users: Dict[str, int]) -> None:
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

    async def handle_matrix_about(self, sender: 'u.User', about: str) -> None:
        if self.peer_type not in ("chat", "channel"):
            return
        peer = await self.get_input_entity(sender)
        await sender.client(EditChatAboutRequest(peer=peer, about=about))
        self.about = about
        self.save()

    async def handle_matrix_title(self, sender: 'u.User', title: str) -> None:
        if self.peer_type not in ("chat", "channel"):
            return

        if self.peer_type == "chat":
            response = await sender.client(EditChatTitleRequest(chat_id=self.tgid, title=title))
        else:
            channel = await self.get_input_entity(sender)
            response = await sender.client(EditTitleRequest(channel=channel, title=title))
        self.dedup.register_outgoing_actions(response)
        self.title = title
        self.save()

    async def handle_matrix_avatar(self, sender: 'u.User', url: ContentURI) -> None:
        if self.peer_type not in ("chat", "channel"):
            # Invalid peer type
            return

        file = await self.main_intent.download_media(url)
        mime = magic.from_buffer(file, mime=True)
        ext = sane_mimetypes.guess_extension(mime)
        uploaded = await sender.client.upload_file(file, file_name=f"avatar{ext}", use_cache=False)
        photo = InputChatUploadedPhoto(file=uploaded)

        if self.peer_type == "chat":
            response = await sender.client(EditChatPhotoRequest(chat_id=self.tgid, photo=photo))
        else:
            channel = await self.get_input_entity(sender)
            response = await sender.client(EditPhotoRequest(channel=channel, photo=photo))
        self.dedup.register_outgoing_actions(response)
        for update in response.updates:
            is_photo_update = (isinstance(update, UpdateNewMessage)
                               and isinstance(update.message, MessageService)
                               and isinstance(update.message.action, MessageActionChatEditPhoto))
            if is_photo_update:
                loc, size = self._get_largest_photo_size(update.message.action.photo)
                self.photo_id = f"{size.location.volume_id}-{size.location.local_id}"
                self.save()
                break

    async def handle_matrix_upgrade(self, new_room: RoomID) -> None:
        old_room = self.mxid
        self.migrate_and_save_matrix(new_room)
        await self.main_intent.join_room(new_room)
        entity = None  # type: TypeInputPeer
        user = None  # type: AbstractUser
        if self.bot and self.has_bot:
            user = self.bot
            entity = await self.get_input_entity(self.bot)
        if not entity:
            user_mxids = await self.main_intent.get_room_members(self.mxid)
            for user_str in user_mxids:
                user_id = UserID(user_str)
                if user_id == self.az.bot_mxid:
                    continue
                user = u.User.get_by_mxid(user_id, create=False)
                if user and user.tgid:
                    entity = await self.get_input_entity(user)
                    if entity:
                        break
        if not entity:
            self.log.error(
                "Failed to fully migrate to upgraded Matrix room: no Telegram user found.")
            return
        users, participants = await self._get_users(self.bot, entity)
        await self.sync_telegram_users(user, users)
        levels = await self.main_intent.get_power_levels(self.mxid)
        await self.update_telegram_participants(participants, levels)
        self.log.info(f"Upgraded room from {old_room} to {self.mxid}")

    def migrate_and_save_matrix(self, new_id: RoomID) -> None:
        try:
            del self.by_mxid[self.mxid]
        except KeyError:
            pass
        self.mxid = new_id
        self.db_instance.update(mxid=self.mxid)
        self.by_mxid[self.mxid] = self
