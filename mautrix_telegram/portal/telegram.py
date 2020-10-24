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
from typing import Awaitable, Dict, List, Optional, Tuple, Union, NamedTuple, TYPE_CHECKING
from abc import ABC
import random
import mimetypes
import codecs
import unicodedata
import base64
import asyncio

from sqlalchemy.exc import IntegrityError

from telethon.tl.patched import Message, MessageService
from telethon.tl.types import (
    Poll, DocumentAttributeFilename, DocumentAttributeSticker, DocumentAttributeVideo,
    MessageMediaPoll, MessageActionChannelCreate, MessageActionChatAddUser,
    MessageActionChatCreate, MessageActionChatDeletePhoto, MessageActionChatDeleteUser,
    MessageActionChatEditPhoto, MessageActionChatEditTitle, MessageActionChatJoinedByLink,
    MessageActionChatMigrateTo, MessageActionGameScore, MessageMediaDocument, MessageMediaGeo,
    MessageMediaPhoto, MessageMediaDice, MessageMediaGame, MessageMediaUnsupported, PeerUser,
    PhotoCachedSize, TypeChannelParticipant, TypeChatParticipant, TypeDocumentAttribute,
    TypeMessageAction, TypePhotoSize, PhotoSize, UpdateChatUserTyping, UpdateUserTyping,
    MessageEntityPre, ChatPhotoEmpty)

from mautrix.appservice import IntentAPI
from mautrix.types import (EventID, UserID, ImageInfo, ThumbnailInfo, RelatesTo, MessageType,
                           EventType, MediaMessageEventContent, TextMessageEventContent,
                           LocationMessageEventContent, Format)
from mautrix.bridge import NotificationDisabler

from ..types import TelegramID
from ..db import Message as DBMessage, TelegramFile as DBTelegramFile
from ..util import sane_mimetypes
from ..context import Context
from ..tgclient import TelegramClient
from .. import puppet as p, user as u, formatter, util
from .base import BasePortal

if TYPE_CHECKING:
    from ..abstract_user import AbstractUser
    from ..config import Config

InviteList = Union[UserID, List[UserID]]
TypeParticipant = Union[TypeChatParticipant, TypeChannelParticipant]
DocAttrs = NamedTuple("DocAttrs", name=Optional[str], mime_type=Optional[str], is_sticker=bool,
                      sticker_alt=Optional[str], width=int, height=int)

config: Optional['Config'] = None


class PortalTelegram(BasePortal, ABC):
    async def handle_telegram_typing(self, user: p.Puppet,
                                     _: Union[UpdateUserTyping, UpdateChatUserTyping]) -> None:
        await user.intent_for(self).set_typing(self.mxid, is_typing=True)

    def _get_external_url(self, evt: Message) -> Optional[str]:
        if self.peer_type == "channel" and self.username is not None:
            return f"https://t.me/{self.username}/{evt.id}"
        elif self.peer_type != "user":
            return f"https://t.me/c/{self.tgid}/{evt.id}"
        return None

    async def _expire_telegram_photo(self, intent: IntentAPI, event_id: EventID, ttl: int) -> None:
        try:
            content = TextMessageEventContent(msgtype=MessageType.NOTICE, body="Photo has expired")
            content.set_edit(event_id)
            await asyncio.sleep(ttl)
            await self._send_message(intent, content)
        except Exception:
            self.log.warning("Failed to expire Telegram photo %s", event_id, exc_info=True)

    async def handle_telegram_photo(self, source: 'AbstractUser', intent: IntentAPI, evt: Message,
                                    relates_to: RelatesTo = None) -> Optional[EventID]:
        media: MessageMediaPhoto = evt.media
        if media.photo is None and media.ttl_seconds:
            return await self._send_message(intent, TextMessageEventContent(
                msgtype=MessageType.NOTICE, body="Photo has expired"))
        loc, largest_size = self._get_largest_photo_size(media.photo)
        if loc is None:
            content = TextMessageEventContent(msgtype=MessageType.TEXT,
                                              body="Failed to bridge image",
                                              external_url=self._get_external_url(evt))
            return await self._send_message(intent, content, timestamp=evt.date)
        file = await util.transfer_file_to_matrix(source.client, intent, loc,
                                                  encrypt=self.encrypted)
        if not file:
            return None
        if self.get_config("inline_images") and (evt.message or evt.fwd_from or evt.reply_to):
            content = await formatter.telegram_to_matrix(
                evt, source, self.main_intent,
                prefix_html=f"<img src='{file.mxc}' alt='Inline Telegram photo'/><br/>",
                prefix_text="Inline image: ")
            content.external_url = self._get_external_url(evt)
            await intent.set_typing(self.mxid, is_typing=False)
            return await self._send_message(intent, content, timestamp=evt.date)
        info = ImageInfo(
            height=largest_size.h, width=largest_size.w, orientation=0, mimetype=file.mime_type,
            size=(len(largest_size.bytes) if (isinstance(largest_size, PhotoCachedSize))
                  else largest_size.size))
        ext = sane_mimetypes.guess_extension(file.mime_type)
        name = f"disappearing_image{ext}" if media.ttl_seconds else f"image{ext}"
        await intent.set_typing(self.mxid, is_typing=False)
        content = MediaMessageEventContent(msgtype=MessageType.IMAGE, info=info,
                                           body=name, relates_to=relates_to,
                                           external_url=self._get_external_url(evt))
        if file.decryption_info:
            content.file = file.decryption_info
        else:
            content.url = file.mxc
        result = await self._send_message(intent, content, timestamp=evt.date)
        if media.ttl_seconds:
            self.loop.create_task(self._expire_telegram_photo(intent, result,
                                                              media.ttl_seconds))
        if evt.message:
            caption_content = await formatter.telegram_to_matrix(evt, source, self.main_intent,
                                                                 no_reply_fallback=True)
            caption_content.external_url = content.external_url
            result = await self._send_message(intent, caption_content, timestamp=evt.date)
        return result

    @staticmethod
    def _parse_telegram_document_attributes(attributes: List[TypeDocumentAttribute]) -> DocAttrs:
        name, mime_type, is_sticker, sticker_alt, width, height = None, None, False, None, 0, 0
        for attr in attributes:
            if isinstance(attr, DocumentAttributeFilename):
                name = name or attr.file_name
                mime_type, _ = mimetypes.guess_type(attr.file_name)
            elif isinstance(attr, DocumentAttributeSticker):
                is_sticker = True
                sticker_alt = attr.alt
            elif isinstance(attr, DocumentAttributeVideo):
                width, height = attr.w, attr.h
        return DocAttrs(name, mime_type, is_sticker, sticker_alt, width, height)

    @staticmethod
    def _parse_telegram_document_meta(evt: Message, file: DBTelegramFile, attrs: DocAttrs,
                                      thumb_size: TypePhotoSize) -> Tuple[ImageInfo, str]:
        document = evt.media.document
        name = attrs.name
        if attrs.is_sticker:
            alt = attrs.sticker_alt
            if len(alt) > 0:
                try:
                    name = f"{alt} ({unicodedata.name(alt[0]).lower()})"
                except ValueError:
                    name = alt

        generic_types = ("text/plain", "application/octet-stream")
        if file.mime_type in generic_types and document.mime_type not in generic_types:
            mime_type = document.mime_type or file.mime_type
        elif file.mime_type == 'application/ogg':
            mime_type = 'audio/ogg'
        else:
            mime_type = file.mime_type or document.mime_type
        info = ImageInfo(size=file.size, mimetype=mime_type)

        if attrs.mime_type and not file.was_converted:
            file.mime_type = attrs.mime_type or file.mime_type
        if file.width and file.height:
            info.width, info.height = file.width, file.height
        elif attrs.width and attrs.height:
            info.width, info.height = attrs.width, attrs.height

        if file.thumbnail:
            if file.thumbnail.decryption_info:
                info.thumbnail_file = file.thumbnail.decryption_info
            else:
                info.thumbnail_url = file.thumbnail.mxc
            info.thumbnail_info = ThumbnailInfo(mimetype=file.thumbnail.mime_type,
                                                height=file.thumbnail.height or thumb_size.h,
                                                width=file.thumbnail.width or thumb_size.w,
                                                size=file.thumbnail.size)
        else:
            # This is a hack for bad clients like Riot iOS that require a thumbnail
            if file.decryption_info:
                info.thumbnail_file = file.decryption_info
            else:
                info.thumbnail_url = file.mxc
            info.thumbnail_info = ImageInfo.deserialize(info.serialize())

        return info, name

    async def handle_telegram_document(self, source: 'AbstractUser', intent: IntentAPI,
                                       evt: Message, relates_to: RelatesTo = None
                                       ) -> Optional[EventID]:
        document = evt.media.document

        attrs = self._parse_telegram_document_attributes(document.attributes)

        if document.size > config["bridge.max_document_size"] * 1000 ** 2:
            name = attrs.name or ""
            caption = f"\n{evt.message}" if evt.message else ""
            # TODO encrypt
            return await intent.send_notice(self.mxid, f"Too large file {name}{caption}")

        thumb_loc, thumb_size = self._get_largest_photo_size(document)
        if thumb_size and not isinstance(thumb_size, (PhotoSize, PhotoCachedSize)):
            self.log.debug(f"Unsupported thumbnail type {type(thumb_size)}")
            thumb_loc = None
            thumb_size = None
        parallel_id = source.tgid if config["bridge.parallel_file_transfer"] else None
        file = await util.transfer_file_to_matrix(source.client, intent, document, thumb_loc,
                                                  is_sticker=attrs.is_sticker,
                                                  tgs_convert=config["bridge.animated_sticker"],
                                                  filename=attrs.name, parallel_id=parallel_id,
                                                  encrypt=self.encrypted)
        if not file:
            return None

        info, name = self._parse_telegram_document_meta(evt, file, attrs, thumb_size)

        await intent.set_typing(self.mxid, is_typing=False)

        event_type = EventType.ROOM_MESSAGE
        # Riot only supports images as stickers, so send animated webm stickers as m.video
        if attrs.is_sticker and file.mime_type.startswith("image/"):
            event_type = EventType.STICKER
        content = MediaMessageEventContent(
            body=name or "unnamed file", info=info, relates_to=relates_to,
            external_url=self._get_external_url(evt),
            msgtype={
                "video/": MessageType.VIDEO,
                "audio/": MessageType.AUDIO,
                "image/": MessageType.IMAGE,
            }.get(info.mimetype[:6], MessageType.FILE))
        if file.decryption_info:
            content.file = file.decryption_info
        else:
            content.url = file.mxc
        res = await self._send_message(intent, content, event_type=event_type, timestamp=evt.date)
        if evt.message:
            caption_content = await formatter.telegram_to_matrix(evt, source, self.main_intent,
                                                                 no_reply_fallback=True)
            caption_content.external_url = content.external_url
            res = await self._send_message(intent, caption_content, timestamp=evt.date)
        return res

    def handle_telegram_location(self, source: 'AbstractUser', intent: IntentAPI, evt: Message,
                                 relates_to: RelatesTo = None) -> Awaitable[EventID]:
        long = evt.media.geo.long
        lat = evt.media.geo.lat
        long_char = "E" if long > 0 else "W"
        lat_char = "N" if lat > 0 else "S"
        geo = f"{round(lat, 6)},{round(long, 6)}"

        body = f"{round(abs(lat), 4)}° {lat_char}, {round(abs(long), 4)}° {long_char}"
        url = f"https://maps.google.com/?q={geo}"

        content = LocationMessageEventContent(
            msgtype=MessageType.LOCATION, geo_uri=f"geo:{geo}",
            body=f"Location: {body}\n{url}",
            relates_to=relates_to, external_url=self._get_external_url(evt))
        content["format"] = str(Format.HTML)
        content["formatted_body"] = f"Location: <a href='{url}'>{body}</a>"

        return self._send_message(intent, content, timestamp=evt.date)

    async def handle_telegram_text(self, source: 'AbstractUser', intent: IntentAPI, is_bot: bool,
                                   evt: Message) -> EventID:
        self.log.trace(f"Sending {evt.message} to {self.mxid} by {intent.mxid}")
        content = await formatter.telegram_to_matrix(evt, source, self.main_intent)
        content.external_url = self._get_external_url(evt)
        if is_bot and self.get_config("bot_messages_as_notices"):
            content.msgtype = MessageType.NOTICE
        await intent.set_typing(self.mxid, is_typing=False)
        return await self._send_message(intent, content, timestamp=evt.date)

    async def handle_telegram_unsupported(self, source: 'AbstractUser', intent: IntentAPI,
                                          evt: Message, relates_to: RelatesTo = None) -> EventID:
        override_text = ("This message is not supported on your version of Mautrix-Telegram. "
                         "Please check https://github.com/tulir/mautrix-telegram or ask your "
                         "bridge administrator about possible updates.")
        content = await formatter.telegram_to_matrix(
            evt, source, self.main_intent, override_text=override_text)
        content.msgtype = MessageType.NOTICE
        content.external_url = self._get_external_url(evt)
        content["net.maunium.telegram.unsupported"] = True
        await intent.set_typing(self.mxid, is_typing=False)
        return await self._send_message(intent, content, timestamp=evt.date)

    async def handle_telegram_poll(self, source: 'AbstractUser', intent: IntentAPI, evt: Message,
                                   relates_to: RelatesTo) -> EventID:
        poll: Poll = evt.media.poll
        poll_id = self._encode_msgid(source, evt)

        _n = 0

        def n() -> int:
            nonlocal _n
            _n += 1
            return _n

        text_answers = "\n".join(f"{n()}. {answer.text}" for answer in poll.answers)
        html_answers = "\n".join(f"<li>{answer.text}</li>" for answer in poll.answers)
        content = TextMessageEventContent(
            msgtype=MessageType.TEXT, format=Format.HTML,
            body=f"Poll: {poll.question}\n{text_answers}\n"
                 f"Vote with !tg vote {poll_id} <choice number>",
            formatted_body=f"<strong>Poll</strong>: {poll.question}<br/>\n"
                           f"<ol>{html_answers}</ol>\n"
                           f"Vote with <code>!tg vote {poll_id} &lt;choice number&gt;</code>",
            relates_to=relates_to, external_url=self._get_external_url(evt))

        await intent.set_typing(self.mxid, is_typing=False)
        return await self._send_message(intent, content, timestamp=evt.date)

    async def handle_telegram_dice(self, source: 'AbstractUser', intent: IntentAPI, evt: Message,
                                   relates_to: RelatesTo) -> EventID:
        emoji_text = {
            "\U0001F3AF": " Dart throw",
            "\U0001F3B2": " Dice roll",
            "\U0001F3C0": " Basketball throw",
            "\u26BD": " Football kick"
        }
        roll: MessageMediaDice = evt.media
        text = f"{roll.emoticon}{emoji_text.get(roll.emoticon, '')} result: {roll.value}"
        content = TextMessageEventContent(msgtype=MessageType.TEXT, format=Format.HTML, body=text,
                                          formatted_body=f"<h4>{text}</h4>", relates_to=relates_to,
                                          external_url=self._get_external_url(evt))
        content["net.maunium.telegram.dice"] = {"emoticon": roll.emoticon, "value": roll.value}
        await intent.set_typing(self.mxid, is_typing=False)
        return await self._send_message(intent, content, timestamp=evt.date)

    @staticmethod
    def _int_to_bytes(i: int) -> bytes:
        hex_value = "{0:010x}".format(i).encode("utf-8")
        return codecs.decode(hex_value, "hex_codec")

    def _encode_msgid(self, source: 'AbstractUser', evt: Message) -> str:
        if self.peer_type == "channel":
            play_id = (b"c"
                       + self._int_to_bytes(self.tgid)
                       + self._int_to_bytes(evt.id))
        elif self.peer_type == "chat":
            play_id = (b"g"
                       + self._int_to_bytes(self.tgid)
                       + self._int_to_bytes(evt.id)
                       + self._int_to_bytes(source.tgid))
        elif self.peer_type == "user":
            play_id = (b"u"
                       + self._int_to_bytes(self.tgid)
                       + self._int_to_bytes(evt.id))
        else:
            raise ValueError("Portal has invalid peer type")
        return base64.b64encode(play_id).decode("utf-8").rstrip("=")

    async def handle_telegram_game(self, source: 'AbstractUser', intent: IntentAPI,
                                   evt: Message, relates_to: RelatesTo = None) -> EventID:
        game = evt.media.game
        play_id = self._encode_msgid(source, evt)
        command = f"!tg play {play_id}"
        override_text = f"Run {command} in your bridge management room to play {game.title}"
        override_entities = [
            MessageEntityPre(offset=len("Run "), length=len(command), language="")]

        content = await formatter.telegram_to_matrix(
            evt, source, self.main_intent,
            override_text=override_text, override_entities=override_entities)
        content.msgtype = MessageType.NOTICE
        content.external_url = self._get_external_url(evt)
        content["net.maunium.telegram.game"] = play_id

        await intent.set_typing(self.mxid, is_typing=False)
        return await self._send_message(intent, content, timestamp=evt.date)

    async def handle_telegram_edit(self, source: 'AbstractUser', sender: p.Puppet, evt: Message
                                   ) -> None:
        if not self.mxid:
            self.log.trace("Ignoring edit to %d as chat has no Matrix room", evt.id)
            return
        elif hasattr(evt, "media") and isinstance(evt.media, MessageMediaGame):
            self.log.debug("Ignoring game message edit event")
            return

        async with self.send_lock(sender.tgid if sender else None, required=False):
            tg_space = self.tgid if self.peer_type == "channel" else source.tgid

            temporary_identifier = EventID(
                f"${random.randint(1000000000000, 9999999999999)}TGBRIDGEDITEMP")
            duplicate_found = self.dedup.check(evt, (temporary_identifier, tg_space),
                                               force_hash=True)
            if duplicate_found:
                mxid, other_tg_space = duplicate_found
                if tg_space != other_tg_space:
                    prev_edit_msg = DBMessage.get_one_by_tgid(TelegramID(evt.id), tg_space, -1)
                    if not prev_edit_msg:
                        return
                    DBMessage(mxid=mxid, mx_room=self.mxid, tg_space=tg_space,
                              tgid=TelegramID(evt.id), edit_index=prev_edit_msg.edit_index + 1
                              ).insert()
                return

        content = await formatter.telegram_to_matrix(evt, source, self.main_intent,
                                                     no_reply_fallback=True)
        editing_msg = DBMessage.get_one_by_tgid(TelegramID(evt.id), tg_space)
        if not editing_msg:
            self.log.info(f"Didn't find edited message {evt.id}@{tg_space} (src {source.tgid}) "
                          "in database.")
            return

        content.msgtype = (MessageType.NOTICE if (sender and sender.is_bot
                                                  and self.get_config("bot_messages_as_notices"))
                           else MessageType.TEXT)
        content.external_url = self._get_external_url(evt)
        content.set_edit(editing_msg.mxid)

        intent = sender.intent_for(self) if sender else self.main_intent
        await intent.set_typing(self.mxid, is_typing=False)
        event_id = await self._send_message(intent, content)

        prev_edit_msg = DBMessage.get_one_by_tgid(TelegramID(evt.id), tg_space, -1) or editing_msg
        DBMessage(mxid=event_id, mx_room=self.mxid, tg_space=tg_space, tgid=TelegramID(evt.id),
                  edit_index=prev_edit_msg.edit_index + 1).insert()
        DBMessage.update_by_mxid(temporary_identifier, self.mxid, mxid=event_id)

    @property
    def _takeout_options(self) -> Dict[str, Union[bool, int]]:
        return {
            "files": True,
            "megagroups": self.megagroup,
            "chats": self.peer_type == "chat",
            "users": self.peer_type == "user",
            "channels": (self.peer_type == "channel" and not self.megagroup),
            "max_file_size": min(config["bridge.max_document_size"], 2000) * 1024 * 1024
        }

    async def backfill(self, source: 'u.User', is_initial: bool = False,
                       limit: Optional[int] = None, last_id: Optional[int] = None) -> None:
        async with self.backfill_method_lock:
            await self._locked_backfill(source, is_initial, limit, last_id)

    async def _locked_backfill(self, source: 'u.User', is_initial: bool = False,
                               limit: Optional[int] = None, last_id: Optional[int] = None) -> None:
        limit = limit or (config["bridge.backfill.initial_limit"] if is_initial
                          else config["bridge.backfill.missed_limit"])
        if limit == 0:
            return
        if not config["bridge.backfill.normal_groups"] and self.peer_type == "chat":
            return
        last = DBMessage.find_last(self.mxid, (source.tgid if self.peer_type != "channel"
                                               else self.tgid))
        min_id = last.tgid if last else 0
        if last_id is None:
            messages = await source.client.get_messages(self.peer, limit=1)
            if not messages:
                # The chat seems empty
                return
            last_id = messages[0].id
        if last_id <= min_id:
            # Nothing to backfill
            return
        if limit < 0:
            limit = last_id - min_id
            self.log.debug(f"Backfilling approximately {last_id - min_id} messages "
                           f"through {source.mxid}")
        elif self.peer_type == "channel":
            # This is a channel or supergroup, so we'll backfill messages based on the ID.
            # There are some cases, such as deleted messages, where this may backfill less
            # messages than the limit.
            min_id = max(last_id - limit, min_id)
            self.log.debug(f"Backfilling messages after ID {min_id} (last message: {last_id}) "
                           f"through {source.mxid}")
        else:
            # Private chats and normal groups don't have their own message ID namespace,
            # which means we'll have to fetch messages a different way.
            # The _backfill_messages method will detect min_id=None and not use reverse=True
            min_id = None
            self.log.debug(f"Backfilling up to {limit} messages through {source.mxid}")
        with self.backfill_lock:
            await self._backfill(source, min_id, limit)

    async def _backfill(self, source: 'u.User', min_id: Optional[int], limit: int) -> None:
        self.backfill_leave = set()
        if ((self.peer_type == "user" and self.tgid != source.tgid
             and config["bridge.backfill.invite_own_puppet"])):
            self.log.debug("Adding %s's default puppet to room for backfilling", source.mxid)
            sender = p.Puppet.get(source.tgid)
            await self.main_intent.invite_user(self.mxid, sender.default_mxid)
            await sender.default_mxid_intent.join_room_by_id(self.mxid)
            self.backfill_leave.add(sender.default_mxid_intent)

        client = source.client
        async with NotificationDisabler(self.mxid, source):
            if limit > config["bridge.backfill.takeout_limit"]:
                self.log.debug(f"Opening takeout client for {source.tgid}")
                async with client.takeout(**self._takeout_options) as takeout:
                    count = await self._backfill_messages(source, min_id, limit, takeout)
            else:
                count = await self._backfill_messages(source, min_id, limit, client)

        for intent in self.backfill_leave:
            self.log.trace("Leaving room with %s post-backfill", intent.mxid)
            await intent.leave_room(self.mxid)
        self.backfill_leave = None
        self.log.info("Backfilled %d messages through %s", count, source.mxid)

    async def _backfill_messages(self, source: 'AbstractUser', min_id: Optional[int], limit: int,
                                 client: TelegramClient) -> int:
        count = 0
        entity = await self.get_input_entity(source)
        if min_id is not None:
            self.log.debug(f"Iterating all messages starting with {min_id} (approx: {limit})")
            messages = client.iter_messages(entity, reverse=True, min_id=min_id)
            async for message in messages:
                sender = (p.Puppet.get(message.from_id.user_id)
                          if isinstance(message.from_id, PeerUser) else None)
                # TODO handle service messages?
                await self.handle_telegram_message(source, sender, message)
                count += 1
        else:
            self.log.debug(f"Fetching up to {limit} most recent messages")
            messages = await client.get_messages(entity, limit=limit)
            for message in reversed(messages):
                sender = (p.Puppet.get(TelegramID(message.from_id.user_id))
                          if isinstance(message.from_id, PeerUser) else None)
                await self.handle_telegram_message(source, sender, message)
                count += 1
        return count

    async def handle_telegram_message(self, source: 'AbstractUser', sender: p.Puppet,
                                      evt: Message) -> None:
        if not self.mxid:
            self.log.trace("Got telegram message %d, but no room exists, creating...", evt.id)
            await self.create_matrix_room(source, invites=[source.mxid], update_if_exists=False)

        if (self.peer_type == "user" and sender and sender.tgid == self.tg_receiver
            and not sender.is_real_user and not await self.az.state_store.is_joined(self.mxid,
                                                                                    sender.mxid)):
            self.log.debug(f"Ignoring private chat message {evt.id}@{source.tgid} as receiver does"
                           " not have matrix puppeting and their default puppet isn't in the room")
            return

        async with self.send_lock(sender.tgid if sender else None, required=False):
            tg_space = self.tgid if self.peer_type == "channel" else source.tgid

            temporary_identifier = EventID(
                f"${random.randint(1000000000000, 9999999999999)}TGBRIDGETEMP")
            duplicate_found = self.dedup.check(evt, (temporary_identifier, tg_space))
            if duplicate_found:
                mxid, other_tg_space = duplicate_found
                self.log.debug(f"Ignoring message {evt.id}@{tg_space} (src {source.tgid}) "
                               f"as it was already handled (in space {other_tg_space})")
                if tg_space != other_tg_space:
                    DBMessage(tgid=TelegramID(evt.id), mx_room=self.mxid, mxid=mxid,
                              tg_space=tg_space, edit_index=0).insert()
                return

        if self.backfill_lock.locked or (self.dedup.pre_db_check and self.peer_type == "channel"):
            msg = DBMessage.get_one_by_tgid(TelegramID(evt.id), tg_space)
            if msg:
                self.log.debug(f"Ignoring message {evt.id} (src {source.tgid}) as it was already "
                               f"handled into {msg.mxid}. This duplicate was catched in the db "
                               "check. If you get this message often, consider increasing "
                               "bridge.deduplication.cache_queue_length in the config.")
                return

        self.log.trace("Handling Telegram message %s", evt)

        if sender and not sender.displayname:
            self.log.debug(f"Telegram user {sender.tgid} sent a message, but doesn't have a "
                           "displayname, updating info...")
            entity = await source.client.get_entity(PeerUser(sender.tgid))
            await sender.update_info(source, entity)

        allowed_media = (MessageMediaPhoto, MessageMediaDocument, MessageMediaGeo,
                         MessageMediaGame, MessageMediaDice, MessageMediaPoll,
                         MessageMediaUnsupported)
        media = evt.media if hasattr(evt, "media") and isinstance(evt.media,
                                                                  allowed_media) else None
        if sender:
            intent = sender.intent_for(self)
            if ((self.backfill_lock.locked and intent != sender.default_mxid_intent
                 and config["bridge.backfill.invite_own_puppet"])):
                intent = sender.default_mxid_intent
                self.backfill_leave.add(intent)
        else:
            intent = self.main_intent
        if not media and evt.message:
            is_bot = sender.is_bot if sender else False
            event_id = await self.handle_telegram_text(source, intent, is_bot, evt)
        elif media:
            event_id = await {
                MessageMediaPhoto: self.handle_telegram_photo,
                MessageMediaDocument: self.handle_telegram_document,
                MessageMediaGeo: self.handle_telegram_location,
                MessageMediaPoll: self.handle_telegram_poll,
                MessageMediaDice: self.handle_telegram_dice,
                MessageMediaUnsupported: self.handle_telegram_unsupported,
                MessageMediaGame: self.handle_telegram_game,
            }[type(media)](source, intent, evt,
                           relates_to=formatter.telegram_reply_to_matrix(evt, source))
        else:
            self.log.debug("Unhandled Telegram message %d", evt.id)
            return

        if not event_id:
            return

        prev_id = self.dedup.update(evt, (event_id, tg_space), (temporary_identifier, tg_space))
        if prev_id:
            self.log.debug(f"Sent message {evt.id}@{tg_space} to Matrix as {event_id}. "
                           f"Temporary dedup identifier was {temporary_identifier}, "
                           f"but dedup map contained {prev_id[1]} instead! -- "
                           "This was probably a race condition caused by Telegram sending updates"
                           "to other clients before responding to the sender. I'll just redact "
                           "the likely duplicate message now.")
            await intent.redact(self.mxid, event_id)
            return

        self.log.debug("Handled telegram message %d -> %s", evt.id, event_id)
        try:
            DBMessage(tgid=TelegramID(evt.id), mx_room=self.mxid, mxid=event_id,
                      tg_space=tg_space, edit_index=0).insert()
            DBMessage.update_by_mxid(temporary_identifier, self.mxid, mxid=event_id)
        except IntegrityError as e:
            self.log.exception(f"{e.__class__.__name__} while saving message mapping. "
                               "This might mean that an update was handled after it left the "
                               "dedup cache queue. You can try enabling bridge.deduplication."
                               "pre_db_check in the config.")
            await intent.redact(self.mxid, event_id)
        await self._send_delivery_receipt(event_id)

    async def _create_room_on_action(self, source: 'AbstractUser',
                                     action: TypeMessageAction) -> bool:
        if source.is_relaybot and config["bridge.ignore_unbridged_group_chat"]:
            return False
        create_and_exit = (MessageActionChatCreate, MessageActionChannelCreate)
        create_and_continue = (MessageActionChatAddUser, MessageActionChatJoinedByLink)
        if isinstance(action, create_and_exit) or isinstance(action, create_and_continue):
            await self.create_matrix_room(source, invites=[source.mxid],
                                          update_if_exists=isinstance(action, create_and_exit))
        if not isinstance(action, create_and_continue):
            return False
        return True

    async def handle_telegram_action(self, source: 'AbstractUser', sender: p.Puppet,
                                     update: MessageService) -> None:
        action = update.action
        should_ignore = ((not self.mxid and not await self._create_room_on_action(source, action))
                         or self.dedup.check_action(update))
        if should_ignore or not self.mxid:
            return
        if isinstance(action, MessageActionChatEditTitle):
            await self._update_title(action.title, sender=sender, save=True)
            await self.update_bridge_info()
        elif isinstance(action, MessageActionChatEditPhoto):
            await self._update_avatar(source, action.photo, sender=sender, save=True)
            await self.update_bridge_info()
        elif isinstance(action, MessageActionChatDeletePhoto):
            await self._update_avatar(source, ChatPhotoEmpty(), sender=sender, save=True)
            await self.update_bridge_info()
        elif isinstance(action, MessageActionChatAddUser):
            for user_id in action.users:
                await self._add_telegram_user(TelegramID(user_id), source)
        elif isinstance(action, MessageActionChatJoinedByLink):
            await self._add_telegram_user(sender.id, source)
        elif isinstance(action, MessageActionChatDeleteUser):
            await self._delete_telegram_user(TelegramID(action.user_id), sender)
        elif isinstance(action, MessageActionChatMigrateTo):
            self.peer_type = "channel"
            self._migrate_and_save_telegram(TelegramID(action.channel_id))
            # TODO encrypt
            await sender.intent_for(self).send_emote(self.mxid,
                                                     "upgraded this group to a supergroup.")
            await self.update_bridge_info()
        elif isinstance(action, MessageActionGameScore):
            # TODO handle game score
            pass
        else:
            self.log.trace("Unhandled Telegram action in %s: %s", self.title, action)

    async def set_telegram_admin(self, user_id: TelegramID) -> None:
        puppet = p.Puppet.get(user_id)
        user = u.User.get_by_tgid(user_id)

        levels = await self.main_intent.get_power_levels(self.mxid)
        if user:
            levels.users[user.mxid] = 50
        if puppet:
            levels.users[puppet.mxid] = 50
        await self.main_intent.set_power_levels(self.mxid, levels)

    async def receive_telegram_pin_id(self, msg_id: TelegramID, receiver: TelegramID) -> None:
        tg_space = receiver if self.peer_type != "channel" else self.tgid
        message = DBMessage.get_one_by_tgid(msg_id, tg_space) if msg_id != 0 else None
        if message:
            await self.main_intent.set_pinned_messages(self.mxid, [message.mxid])
        else:
            await self.main_intent.set_pinned_messages(self.mxid, [])

    async def set_telegram_admins_enabled(self, enabled: bool) -> None:
        level = 50 if enabled else 10
        levels = await self.main_intent.get_power_levels(self.mxid)
        levels.invite = level
        levels.events[EventType.ROOM_NAME] = level
        levels.events[EventType.ROOM_AVATAR] = level
        await self.main_intent.set_power_levels(self.mxid, levels)


def init(context: Context) -> None:
    global config
    config = context.config
    NotificationDisabler.puppet_cls = p.Puppet
    NotificationDisabler.config_enabled = config["bridge.backfill.disable_notifications"]
