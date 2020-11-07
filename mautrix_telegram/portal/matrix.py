# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2020 Tulir Asokan
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
from typing import Awaitable, Dict, Optional, Union, Any, TYPE_CHECKING
from html import escape as escape_html
from string import Template
from abc import ABC

import magic

from telethon.tl.functions.messages import (EditChatPhotoRequest, EditChatTitleRequest,
                                            UpdatePinnedMessageRequest, SetTypingRequest,
                                            EditChatAboutRequest)
from telethon.tl.functions.channels import EditPhotoRequest, EditTitleRequest, JoinChannelRequest
from telethon.errors import (ChatNotModifiedError, PhotoExtInvalidError,
                             PhotoInvalidDimensionsError, PhotoSaveFileInvalidError,
                             RPCError)
from telethon.tl.patched import Message, MessageService
from telethon.tl.types import (DocumentAttributeFilename, DocumentAttributeImageSize, GeoPoint,
                               InputChatUploadedPhoto, MessageActionChatEditPhoto, MessageMediaGeo,
                               SendMessageCancelAction, SendMessageTypingAction, TypeInputPeer,
                               UpdateNewMessage, InputMediaUploadedDocument,
                               InputMediaUploadedPhoto)

from mautrix.types import (EventID, RoomID, UserID, ContentURI, MessageType, MessageEventContent,
                           TextMessageEventContent, MediaMessageEventContent, Format,
                           LocationMessageEventContent, ImageInfo, VideoInfo)

from ..types import TelegramID
from ..db import Message as DBMessage
from ..util import sane_mimetypes, parallel_transfer_to_telegram
from ..context import Context
from .. import puppet as p, user as u, formatter, util
from .base import BasePortal

if TYPE_CHECKING:
    from ..abstract_user import AbstractUser
    from ..tgclient import MautrixTelegramClient
    from ..config import Config

try:
    from mautrix.crypto.attachments import decrypt_attachment
except ImportError:
    decrypt_attachment = None

TypeMessage = Union[Message, MessageService]

config: Optional['Config'] = None


class PortalMatrix(BasePortal, ABC):
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
        elif self.peer_type == "user" and not config["bridge.relaybot.private_chat.state_changes"]:
            return
        async with self.send_lock(self.bot.tgid):
            message = await self._get_state_change_message(event, user, **kwargs)
            if not message:
                return
            message, entities = await formatter.matrix_to_telegram(self.bot.client, html=message)
            response = await self.bot.client.send_message(self.peer, message,
                                                          formatting_entities=entities)
            space = self.tgid if self.peer_type == "channel" else self.bot.tgid
            self.dedup.check(response, (event_id, space))

    async def name_change_matrix(self, user: 'u.User', displayname: str, prev_displayname: str,
                                 event_id: EventID) -> None:
        await self._send_state_change_message("name_change", user, event_id,
                                              displayname=displayname,
                                              prev_displayname=prev_displayname)

    async def get_displayname(self, user: 'u.User') -> str:
        return await self.main_intent.get_room_displayname(self.mxid, user.mxid) or user.mxid

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
        await user.client.send_read_acknowledge(self.peer, max_id=message.tgid,
                                                clear_mentions=True)

    async def _preproc_kick_ban(self, user: Union['u.User', 'p.Puppet'], source: 'u.User'
                                ) -> Optional['AbstractUser']:
        if user.tgid == source.tgid:
            return None
        if self.peer_type == "user" and user.tgid == self.tgid:
            await self.delete()
            return None
        if isinstance(user, u.User) and await user.needs_relaybot(self):
            if not self.bot:
                return None
            # TODO kick message
            return None
        if await source.needs_relaybot(self):
            if not self.has_bot:
                return None
            return self.bot
        return source

    async def kick_matrix(self, user: Union['u.User', 'p.Puppet'], source: 'u.User') -> None:
        source = await self._preproc_kick_ban(user, source)
        if source is not None:
            await source.client.kick_participant(self.peer, user.peer)

    async def ban_matrix(self, user: Union['u.User', 'p.Puppet'], source: 'u.User'):
        source = await self._preproc_kick_ban(user, source)
        if source is not None:
            await source.client.edit_permissions(self.peer, user.peer, view_messages=False)

    async def leave_matrix(self, user: 'u.User', event_id: EventID) -> None:
        if await user.needs_relaybot(self):
            await self._send_state_change_message("leave", user, event_id)
            return

        if self.peer_type == "user":
            await self.main_intent.leave_room(self.mxid)
            await self.delete()
            try:
                del self.by_tgid[self.tgid_full]
                del self.by_mxid[self.mxid]
            except KeyError:
                pass
        else:
            await user.client.delete_dialog(self.peer)

    async def join_matrix(self, user: 'u.User', event_id: EventID) -> None:
        if await user.needs_relaybot(self):
            await self._send_state_change_message("join", user, event_id)
            return

        if self.peer_type == "channel" and not user.is_bot:
            await user.client(JoinChannelRequest(channel=await self.get_input_entity(user)))
        else:
            # We'll just assume the user is already in the chat.
            pass

    async def _apply_msg_format(self, sender: 'u.User', content: MessageEventContent
                                ) -> None:
        if not isinstance(content, TextMessageEventContent) or content.format != Format.HTML:
            content.format = Format.HTML
            content.formatted_body = escape_html(content.body).replace("\n", "<br/>")

        tpl = (self.get_config(f"message_formats.[{content.msgtype.value}]")
               or "<b>$sender_displayname</b>: $message")
        displayname = await self.get_displayname(sender)
        tpl_args = dict(sender_mxid=sender.mxid,
                        sender_username=sender.mxid_localpart,
                        sender_displayname=escape_html(displayname),
                        message=content.formatted_body,
                        body=content.body, formatted_body=content.formatted_body)
        content.formatted_body = Template(tpl).safe_substitute(tpl_args)

    async def _apply_emote_format(self, sender: 'u.User',
                                  content: TextMessageEventContent) -> None:
        if content.format != Format.HTML:
            content.format = Format.HTML
            content.formatted_body = escape_html(content.body).replace("\n", "<br/>")

        tpl = self.get_config("emote_format")
        puppet = p.Puppet.get(sender.tgid)
        content.formatted_body = Template(tpl).safe_substitute(
            dict(sender_mxid=sender.mxid,
                 sender_username=sender.mxid_localpart,
                 sender_displayname=escape_html(await self.get_displayname(sender)),
                 mention=f"<a href='https://matrix.to/#/{puppet.mxid}'>{puppet.displayname}</a>",
                 username=sender.username,
                 displayname=puppet.displayname,
                 body=content.body,
                 formatted_body=content.formatted_body))
        content.msgtype = MessageType.TEXT

    async def _pre_process_matrix_message(self, sender: 'u.User', use_relaybot: bool,
                                          content: MessageEventContent) -> None:
        if use_relaybot:
            await self._apply_msg_format(sender, content)
        elif content.msgtype == MessageType.EMOTE:
            await self._apply_emote_format(sender, content)

    async def _handle_matrix_text(self, sender_id: TelegramID, event_id: EventID,
                                  space: TelegramID, client: 'MautrixTelegramClient',
                                  content: TextMessageEventContent, reply_to: TelegramID) -> None:
        message, entities = await formatter.matrix_to_telegram(client, text=content.body,
                                                               html=content.formatted(Format.HTML))
        async with self.send_lock(sender_id):
            lp = self.get_config("telegram_link_preview")
            if content.get_edit():
                orig_msg = DBMessage.get_by_mxid(content.get_edit(), self.mxid, space)
                if orig_msg:
                    response = await client.edit_message(self.peer, orig_msg.tgid, message,
                                                         formatting_entities=entities,
                                                         link_preview=lp)
                    self._add_telegram_message_to_db(event_id, space, -1, response)
                    return
            response = await client.send_message(self.peer, message, reply_to=reply_to,
                                                 formatting_entities=entities,
                                                 link_preview=lp)
            self._add_telegram_message_to_db(event_id, space, 0, response)
        await self._send_delivery_receipt(event_id)

    async def _handle_matrix_file(self, sender_id: TelegramID, event_id: EventID,
                                  space: TelegramID, client: 'MautrixTelegramClient',
                                  content: MediaMessageEventContent, reply_to: TelegramID,
                                  caption: TextMessageEventContent = None) -> None:
        mime = content.info.mimetype
        if isinstance(content.info, (ImageInfo, VideoInfo)):
            w, h = content.info.width, content.info.height
        else:
            w = h = None
        file_name = content["net.maunium.telegram.internal.filename"]
        max_image_size = config["bridge.image_as_file_size"] * 1000 ** 2

        if config["bridge.parallel_file_transfer"] and content.url:
            file_handle, file_size = await parallel_transfer_to_telegram(client, self.main_intent,
                                                                         content.url, sender_id)
        else:
            if content.file:
                if not decrypt_attachment:
                    self.log.warning(f"Can't bridge encrypted media event {event_id}:"
                                     " matrix-nio not installed")
                    return
                file = await self.main_intent.download_media(content.file.url)
                file = decrypt_attachment(file, content.file.key.key,
                                          content.file.hashes.get("sha256"), content.file.iv)
            else:
                file = await self.main_intent.download_media(content.url)

            if content.msgtype == MessageType.STICKER:
                if mime != "image/gif":
                    mime, file, w, h = util.convert_image(file, source_mime=mime,
                                                          target_type="webp")
                else:
                    # Remove sticker description
                    file_name = "sticker.gif"

            file_handle = await client.upload_file(file)
            file_size = len(file)

        file_handle.name = file_name

        attributes = [DocumentAttributeFilename(file_name=file_name)]
        if w and h:
            attributes.append(DocumentAttributeImageSize(w, h))

        if (mime == "image/png" or mime == "image/jpeg") and file_size < max_image_size:
            media = InputMediaUploadedPhoto(file_handle)
        else:
            media = InputMediaUploadedDocument(file=file_handle, attributes=attributes,
                                               mime_type=mime or "application/octet-stream")

        capt, entities = (await formatter.matrix_to_telegram(client, text=caption.body,
                                                             html=caption.formatted(Format.HTML))
                          if caption else (None, None))

        async with self.send_lock(sender_id):
            if await self._matrix_document_edit(client, content, space, capt, media, event_id):
                return
            try:
                response = await client.send_media(self.peer, media, reply_to=reply_to,
                                                   caption=capt, entities=entities)
            except (PhotoInvalidDimensionsError, PhotoSaveFileInvalidError, PhotoExtInvalidError):
                media = InputMediaUploadedDocument(file=media.file, mime_type=mime,
                                                   attributes=attributes)
                response = await client.send_media(self.peer, media, reply_to=reply_to,
                                                   caption=capt, entities=entities)
            self._add_telegram_message_to_db(event_id, space, 0, response)
        await self._send_delivery_receipt(event_id)

    async def _matrix_document_edit(self, client: 'MautrixTelegramClient',
                                    content: MessageEventContent, space: TelegramID,
                                    caption: str, media: Any, event_id: EventID) -> bool:
        if content.get_edit():
            orig_msg = DBMessage.get_by_mxid(content.get_edit(), self.mxid, space)
            if orig_msg:
                response = await client.edit_message(self.peer, orig_msg.tgid,
                                                     caption, file=media)
                self._add_telegram_message_to_db(event_id, space, -1, response)
                await self._send_delivery_receipt(event_id)
                return True
        return False

    async def _handle_matrix_location(self, sender_id: TelegramID, event_id: EventID,
                                      space: TelegramID, client: 'MautrixTelegramClient',
                                      content: LocationMessageEventContent, reply_to: TelegramID
                                      ) -> None:
        try:
            lat, long = content.geo_uri[len("geo:"):].split(",")
            lat, long = float(lat), float(long)
        except (KeyError, ValueError):
            self.log.exception("Failed to parse location")
            return None
        caption, entities = await formatter.matrix_to_telegram(client, text=content.body)
        media = MessageMediaGeo(geo=GeoPoint(lat, long, access_hash=0))

        async with self.send_lock(sender_id):
            if await self._matrix_document_edit(client, content, space, caption, media, event_id):
                return
            response = await client.send_media(self.peer, media, reply_to=reply_to,
                                               caption=caption, entities=entities)
            self._add_telegram_message_to_db(event_id, space, 0, response)
        await self._send_delivery_receipt(event_id)

    def _add_telegram_message_to_db(self, event_id: EventID, space: TelegramID,
                                    edit_index: int, response: TypeMessage) -> None:
        self.log.trace("Handled Matrix message: %s", response)
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

    async def _send_bridge_error(self, msg: str) -> None:
        if config["bridge.delivery_error_reports"]:
            await self._send_message(self.main_intent,
                                     TextMessageEventContent(msgtype=MessageType.NOTICE, body=msg))

    async def handle_matrix_message(self, sender: 'u.User', content: MessageEventContent,
                                    event_id: EventID) -> None:
        try:
            await self._handle_matrix_message(sender, content, event_id)
        except RPCError as e:
            if config["bridge.delivery_error_reports"]:
                await self._send_bridge_error(
                    f"\u26a0 Your message may not have been bridged: {e}")
            raise

    async def _handle_matrix_message(self, sender: 'u.User', content: MessageEventContent,
                                     event_id: EventID) -> None:
        if not content.body or not content.msgtype:
            self.log.debug(f"Ignoring message {event_id} in {self.mxid} without body or msgtype")
            return

        logged_in = not await sender.needs_relaybot(self)
        client = sender.client if logged_in else self.bot.client
        sender_id = sender.tgid if logged_in else self.bot.tgid
        space = (self.tgid if self.peer_type == "channel"  # Channels have their own ID space
                 else (sender.tgid if logged_in else self.bot.tgid))
        reply_to = formatter.matrix_reply_to_telegram(content, space, room_id=self.mxid)

        media = (MessageType.STICKER, MessageType.IMAGE, MessageType.FILE, MessageType.AUDIO,
                 MessageType.VIDEO)

        if content.msgtype == MessageType.NOTICE:
            bridge_notices = self.get_config("bridge_notices.default")
            excepted = sender.mxid in self.get_config("bridge_notices.exceptions")
            if not bridge_notices and not excepted:
                return

        if content.msgtype in (MessageType.TEXT, MessageType.EMOTE, MessageType.NOTICE):
            await self._pre_process_matrix_message(sender, not logged_in, content)
            await self._handle_matrix_text(sender_id, event_id, space, client, content, reply_to)
        elif content.msgtype == MessageType.LOCATION:
            await self._pre_process_matrix_message(sender, not logged_in, content)
            await self._handle_matrix_location(sender_id, event_id, space, client, content,
                                               reply_to)
        elif content.msgtype in media:
            content["net.maunium.telegram.internal.filename"] = content.body
            try:
                caption_content: MessageEventContent = sender.command_status["caption"]
                reply_to = reply_to or formatter.matrix_reply_to_telegram(caption_content, space,
                                                                          room_id=self.mxid)
                sender.command_status = None
            except (KeyError, TypeError):
                caption_content = None if logged_in else TextMessageEventContent(body=content.body)
            if caption_content:
                caption_content.msgtype = content.msgtype
                await self._pre_process_matrix_message(sender, not logged_in, caption_content)
            await self._handle_matrix_file(sender_id, event_id, space, client, content, reply_to,
                                           caption_content)
        else:
            self.log.trace("Unhandled Matrix event: %s", content)

    async def handle_matrix_pin(self, sender: 'u.User', pinned_message: Optional[EventID],
                                pin_event_id: EventID) -> None:
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
            await self._send_delivery_receipt(pin_event_id)
        except ChatNotModifiedError:
            pass

    async def handle_matrix_deletion(self, deleter: 'u.User', event_id: EventID,
                                     redaction_event_id: EventID) -> None:
        real_deleter = deleter if not await deleter.needs_relaybot(self) else self.bot
        space = self.tgid if self.peer_type == "channel" else real_deleter.tgid
        message = DBMessage.get_by_mxid(event_id, self.mxid, space)
        if not message:
            return
        if message.edit_index == 0:
            await real_deleter.client.delete_messages(self.peer, [message.tgid])
            await self._send_delivery_receipt(redaction_event_id)
        else:
            self.log.debug(f"Ignoring deletion of edit event {message.mxid} in {message.mx_room}")

    async def _update_telegram_power_level(self, sender: 'u.User', user_id: TelegramID,
                                           level: int) -> None:
        moderator = level >= 50
        admin = level >= 75
        await sender.client.edit_admin(self.peer, user_id,
                                       change_info=moderator, post_messages=moderator,
                                       edit_messages=moderator, delete_messages=moderator,
                                       ban_users=moderator, invite_users=moderator,
                                       pin_messages=moderator, add_admins=admin)

    async def handle_matrix_power_levels(self, sender: 'u.User', new_users: Dict[UserID, int],
                                         old_users: Dict[UserID, int], event_id: Optional[EventID]
                                         ) -> None:
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

    async def handle_matrix_about(self, sender: 'u.User', about: str, event_id: EventID) -> None:
        if self.peer_type not in ("chat", "channel"):
            return
        peer = await self.get_input_entity(sender)
        await sender.client(EditChatAboutRequest(peer=peer, about=about))
        self.about = about
        await self.save()
        await self._send_delivery_receipt(event_id)

    async def handle_matrix_title(self, sender: 'u.User', title: str, event_id: EventID) -> None:
        if self.peer_type not in ("chat", "channel"):
            return

        if self.peer_type == "chat":
            response = await sender.client(EditChatTitleRequest(chat_id=self.tgid, title=title))
        else:
            channel = await self.get_input_entity(sender)
            response = await sender.client(EditTitleRequest(channel=channel, title=title))
        self.dedup.register_outgoing_actions(response)
        self.title = title
        await self.save()
        await self._send_delivery_receipt(event_id)
        await self.update_bridge_info()

    async def handle_matrix_avatar(self, sender: 'u.User', url: ContentURI, event_id: EventID
                                   ) -> None:
        if self.peer_type not in ("chat", "channel"):
            # Invalid peer type
            return
        elif self.avatar_url == url:
            return

        self.avatar_url = url
        file = await self.main_intent.download_media(url)
        mime = magic.from_buffer(file, mime=True)
        ext = sane_mimetypes.guess_extension(mime)
        uploaded = await sender.client.upload_file(file, file_name=f"avatar{ext}")
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
                await self.save()
                break
        await self._send_delivery_receipt(event_id)
        await self.update_bridge_info()

    async def handle_matrix_upgrade(self, sender: UserID, new_room: RoomID, event_id: EventID
                                    ) -> None:
        _, server = self.main_intent.parse_user_id(sender)
        old_room = self.mxid
        self.migrate_and_save_matrix(new_room)
        await self.main_intent.join_room(new_room, servers=[server])
        entity: Optional[TypeInputPeer] = None
        user: Optional[AbstractUser] = None
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
            self.log.error("Failed to fully migrate to upgraded Matrix room: "
                           "no Telegram user found.")
            return
        await self.update_matrix_room(user, entity, direct=self.peer_type == "user")
        self.log.info(f"{sender} upgraded room from {old_room} to {self.mxid}")
        await self._send_delivery_receipt(event_id, room_id=old_room)

    def migrate_and_save_matrix(self, new_id: RoomID) -> None:
        try:
            del self.by_mxid[self.mxid]
        except KeyError:
            pass
        self.mxid = new_id
        self.db_instance.edit(mxid=self.mxid)
        self.by_mxid[self.mxid] = self

    async def enable_dm_encryption(self) -> bool:
        ok = await super().enable_dm_encryption()
        if ok:
            try:
                puppet = p.Puppet.get(self.tgid)
                await self.main_intent.set_room_name(self.mxid, puppet.displayname)
            except Exception:
                self.log.warning(f"Failed to set room name", exc_info=True)
        return ok


def init(context: Context) -> None:
    global config
    config = context.config
