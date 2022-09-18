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

from typing import Any, NamedTuple
import base64
import codecs
import html
import mimetypes
import unicodedata

from attr import dataclass
from telethon.tl.types import (
    Document,
    DocumentAttributeAnimated,
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    DocumentAttributeImageSize,
    DocumentAttributeSticker,
    DocumentAttributeVideo,
    Game,
    InputPhotoFileLocation,
    Message,
    MessageEntityPre,
    MessageMediaContact,
    MessageMediaDice,
    MessageMediaDocument,
    MessageMediaGame,
    MessageMediaGeo,
    MessageMediaGeoLive,
    MessageMediaPhoto,
    MessageMediaPoll,
    MessageMediaUnsupported,
    MessageMediaVenue,
    MessageMediaWebPage,
    PeerChannel,
    PeerUser,
    Photo,
    PhotoCachedSize,
    PhotoEmpty,
    PhotoSize,
    PhotoSizeEmpty,
    PhotoSizeProgressive,
    Poll,
    TypeDocumentAttribute,
    TypePhotoSize,
    WebPage,
)
from telethon.utils import decode_waveform

from mautrix.appservice import IntentAPI
from mautrix.types import (
    EventType,
    Format,
    ImageInfo,
    LocationMessageEventContent,
    MediaMessageEventContent,
    MessageEventContent,
    MessageType,
    TextMessageEventContent,
    ThumbnailInfo,
)
from mautrix.util.logging import TraceLogger

from .. import abstract_user as au, formatter, matrix as m, portal as po, puppet as pu, util
from ..config import Config
from ..db import Message as DBMessage, TelegramFile as DBTelegramFile
from ..types import TelegramID
from ..util import sane_mimetypes

try:
    import phonenumbers
except ImportError:
    phonenumbers = None


@dataclass
class ConvertedMessage:
    content: MessageEventContent
    caption: MessageEventContent | None = None
    type: EventType = EventType.ROOM_MESSAGE
    disappear_seconds: int | None = None
    disappear_start_immediately: bool = False


class DocAttrs(NamedTuple):
    name: str | None
    mime_type: str | None
    is_sticker: bool
    sticker_alt: str | None
    width: int
    height: int
    is_gif: bool
    is_audio: bool
    is_voice: bool
    duration: int
    waveform: bytes


BEEPER_LINK_PREVIEWS_KEY = "com.beeper.linkpreviews"
BEEPER_IMAGE_ENCRYPTION_KEY = "beeper:image:encryption"


class TelegramMessageConverter:
    portal: po.Portal
    matrix: m.MatrixHandler
    config: Config
    command_prefix: str
    log: TraceLogger

    def __init__(self, portal: po.Portal) -> None:
        self.portal = portal
        self.matrix = portal.matrix
        self.config = portal.config
        self.command_prefix = self.config["bridge.command_prefix"]
        self.log = portal.log.getChild("msg_conv")

        self._media_converters = {
            MessageMediaPhoto: self._convert_photo,
            MessageMediaDocument: self._convert_document,
            MessageMediaGeo: self._convert_location,
            MessageMediaGeoLive: self._convert_location,
            MessageMediaVenue: self._convert_location,
            MessageMediaPoll: self._convert_poll,
            MessageMediaDice: self._convert_dice,
            MessageMediaUnsupported: self._convert_unsupported,
            MessageMediaGame: self._convert_game,
            MessageMediaContact: self._convert_contact,
        }
        self._allowed_media = tuple(self._media_converters.keys())

    async def convert(
        self,
        source: au.AbstractUser,
        intent: IntentAPI,
        is_bot: bool,
        evt: Message,
        no_reply_fallback: bool = False,
    ) -> ConvertedMessage | None:
        if hasattr(evt, "media") and isinstance(evt.media, self._allowed_media):
            convert_media = self._media_converters[type(evt.media)]
            converted = await convert_media(source=source, intent=intent, evt=evt)
        elif evt.message:
            converted = await self._convert_text(source, intent, is_bot, evt)
        else:
            self.log.debug("Unhandled Telegram message %d", evt.id)
            return
        if converted:
            if evt.ttl_period and not converted.disappear_seconds:
                converted.disappear_seconds = evt.ttl_period
                converted.disappear_start_immediately = True
            converted.content.external_url = self._get_external_url(evt)
            converted.content["fi.mau.telegram.source"] = {
                "space": self.portal.tgid if self.portal.peer_type == "channel" else source.tgid,
                "chat_id": self.portal.tgid,
                "peer_type": self.portal.peer_type,
                "id": evt.id,
            }
            if converted.caption:
                converted.caption["fi.mau.telegram.source"] = converted.content[
                    "fi.mau.telegram.source"
                ]
                converted.caption.external_url = converted.content.external_url
                if self.portal.get_config("caption_in_message"):
                    self._caption_to_message(converted)
            await self._set_reply(source, evt, converted.content, no_fallback=no_reply_fallback)
        return converted

    @staticmethod
    def _caption_to_message(converted: ConvertedMessage) -> None:
        content, caption = converted.content, converted.caption
        converted.caption = None

        content["filename"] = content.body
        content["org.matrix.msc1767.caption"] = {
            "org.matrix.msc1767.text": caption.body,
        }
        content.body = caption.body
        if caption.format == Format.HTML:
            content["org.matrix.msc1767.caption"][
                "org.matrix.msc1767.html"
            ] = caption.formatted_body
            content["formatted_body"] = caption.formatted_body
            content["format"] = Format.HTML.value

    def _get_external_url(self, evt: Message) -> str | None:
        if self.portal.peer_type == "channel" and self.portal.username is not None:
            return f"https://t.me/{self.portal.username}/{evt.id}"
        elif self.portal.peer_type != "user":
            return f"https://t.me/c/{self.portal.tgid}/{evt.id}"
        return None

    @staticmethod
    def _int_to_bytes(i: int) -> bytes:
        return codecs.decode(f"{i:010x}", "hex")

    def _encode_msgid(self, source: au.AbstractUser, evt: Message) -> str:
        if self.portal.peer_type == "channel":
            play_id = b"c" + self._int_to_bytes(self.portal.tgid) + self._int_to_bytes(evt.id)
        elif self.portal.peer_type == "chat":
            play_id = (
                b"g"
                + self._int_to_bytes(self.portal.tgid)
                + self._int_to_bytes(evt.id)
                + self._int_to_bytes(source.tgid)
            )
        elif self.portal.peer_type == "user":
            play_id = b"u" + self._int_to_bytes(self.portal.tgid) + self._int_to_bytes(evt.id)
        else:
            raise ValueError("Portal has invalid peer type")
        return base64.b64encode(play_id).decode("utf-8").rstrip("=")

    async def _set_reply(
        self,
        source: au.AbstractUser,
        evt: Message,
        content: MessageEventContent,
        no_fallback: bool = False,
    ) -> None:
        if not evt.reply_to:
            return
        space = (
            evt.peer_id.channel_id
            if isinstance(evt, Message) and isinstance(evt.peer_id, PeerChannel)
            else source.tgid
        )
        msg = await DBMessage.get_one_by_tgid(TelegramID(evt.reply_to.reply_to_msg_id), space)
        if not msg or msg.mx_room != self.portal.mxid:
            return
        elif not isinstance(content, TextMessageEventContent) or no_fallback:
            # Not a text message, just set the reply metadata and return
            content.set_reply(msg.mxid)
            return

        # Text message, try to fetch original message to generate reply fallback.
        try:
            event = await self.portal.main_intent.get_event(msg.mx_room, msg.mxid)
            if event.type == EventType.ROOM_ENCRYPTED and source.bridge.matrix.e2ee:
                event = await source.bridge.matrix.e2ee.decrypt(event)
            if isinstance(event.content, TextMessageEventContent):
                event.content.trim_reply_fallback()
            puppet = await pu.Puppet.get_by_mxid(event.sender, create=False)
            content.set_reply(event, displayname=puppet.displayname if puppet else event.sender)
        except Exception:
            self.log.exception("Failed to get event to add reply fallback")
            content.set_reply(msg.mxid)

    @staticmethod
    def _photo_size_key(photo: TypePhotoSize) -> int:
        if isinstance(photo, PhotoSize):
            return photo.size
        elif isinstance(photo, PhotoSizeProgressive):
            return max(photo.sizes)
        elif isinstance(photo, PhotoSizeEmpty):
            return 0
        else:
            return len(photo.bytes)

    @classmethod
    def get_largest_photo_size(
        cls, photo: Photo | Document
    ) -> tuple[InputPhotoFileLocation | None, TypePhotoSize | None]:
        if (
            not photo
            or isinstance(photo, PhotoEmpty)
            or (isinstance(photo, Document) and not photo.thumbs)
        ):
            return None, None

        largest = max(
            photo.thumbs if isinstance(photo, Document) else photo.sizes, key=cls._photo_size_key
        )
        return (
            InputPhotoFileLocation(
                id=photo.id,
                access_hash=photo.access_hash,
                file_reference=photo.file_reference,
                thumb_size=largest.type,
            ),
            largest,
        )

    async def _webpage_to_beeper_link_preview(
        self, source: au.AbstractUser, intent: IntentAPI, webpage: WebPage
    ) -> dict[str, Any]:
        beeper_link_preview: dict[str, Any] = {
            "matched_url": webpage.url,
            "og:title": webpage.title,
            "og:url": webpage.url,
            "og:description": webpage.description,
        }

        # Upload an image corresponding to the link preview if it exists.
        if webpage.photo:
            loc, largest_size = self.get_largest_photo_size(webpage.photo)
            if loc is None:
                return beeper_link_preview
            beeper_link_preview["og:image:height"] = largest_size.h
            beeper_link_preview["og:image:width"] = largest_size.w
            file = await util.transfer_file_to_matrix(
                source.client,
                intent,
                loc,
                encrypt=self.portal.encrypted,
                async_upload=self.config["homeserver.async_media"],
            )

            if file.decryption_info:
                beeper_link_preview[BEEPER_IMAGE_ENCRYPTION_KEY] = file.decryption_info.serialize()
            else:
                beeper_link_preview["og:image"] = file.mxc

        return beeper_link_preview

    async def _convert_text(
        self, source: au.AbstractUser, intent: IntentAPI, is_bot: bool, evt: Message
    ) -> ConvertedMessage:
        content = await formatter.telegram_to_matrix(evt, source)
        if is_bot and self.portal.get_config("bot_messages_as_notices"):
            content.msgtype = MessageType.NOTICE

        if (
            hasattr(evt, "media")
            and isinstance(evt.media, MessageMediaWebPage)
            and isinstance(evt.media.webpage, WebPage)
        ):
            content[BEEPER_LINK_PREVIEWS_KEY] = [
                await self._webpage_to_beeper_link_preview(source, intent, evt.media.webpage)
            ]

        return ConvertedMessage(content=content)

    async def _convert_photo(
        self, source: au.AbstractUser, intent: IntentAPI, evt: Message
    ) -> ConvertedMessage | None:
        media: MessageMediaPhoto = evt.media
        if media.photo is None and media.ttl_seconds:
            return ConvertedMessage(
                content=TextMessageEventContent(
                    msgtype=MessageType.NOTICE, body="Photo has expired"
                )
            )
        loc, largest_size = self.get_largest_photo_size(media.photo)
        if loc is None:
            return ConvertedMessage(
                content=TextMessageEventContent(
                    msgtype=MessageType.TEXT,
                    body="Failed to bridge image",
                )
            )
        file = await util.transfer_file_to_matrix(
            source.client,
            intent,
            loc,
            encrypt=self.portal.encrypted,
            async_upload=self.config["homeserver.async_media"],
        )
        if not file:
            return None
        info = ImageInfo(
            height=largest_size.h,
            width=largest_size.w,
            orientation=0,
            mimetype=file.mime_type,
            size=self._photo_size_key(largest_size),
        )
        ext = sane_mimetypes.guess_extension(file.mime_type)
        name = f"disappearing_image{ext}" if media.ttl_seconds else f"image{ext}"
        content = MediaMessageEventContent(
            msgtype=MessageType.IMAGE,
            info=info,
            body=name,
        )
        if file.decryption_info:
            content.file = file.decryption_info
        else:
            content.url = file.mxc
        caption_content = await formatter.telegram_to_matrix(evt, source) if evt.message else None
        return ConvertedMessage(
            content=content,
            caption=caption_content,
            disappear_seconds=media.ttl_seconds,
        )

    async def _convert_document(
        self, source: au.AbstractUser, intent: IntentAPI, evt: Message
    ) -> ConvertedMessage | None:
        document = evt.media.document

        attrs = _parse_document_attributes(document.attributes)

        if document.size > self.matrix.media_config.upload_size:
            name = attrs.name or ""
            caption = f"\n{evt.message}" if evt.message else ""
            return ConvertedMessage(
                content=TextMessageEventContent(
                    msgtype=MessageType.NOTICE, body=f"Too large file {name}{caption}"
                )
            )

        thumb_loc, thumb_size = self.get_largest_photo_size(document)
        if thumb_size and not isinstance(thumb_size, (PhotoSize, PhotoCachedSize)):
            self.log.debug(f"Unsupported thumbnail type {type(thumb_size)}")
            thumb_loc = None
            thumb_size = None
        parallel_id = source.tgid if self.config["bridge.parallel_file_transfer"] else None
        tgs_convert = self.config["bridge.animated_sticker"]
        file = await util.transfer_file_to_matrix(
            source.client,
            intent,
            document,
            thumb_loc,
            is_sticker=attrs.is_sticker,
            tgs_convert=tgs_convert,
            webm_convert=tgs_convert["target"] if tgs_convert["convert_from_webm"] else None,
            filename=attrs.name,
            parallel_id=parallel_id,
            encrypt=self.portal.encrypted,
            async_upload=self.config["homeserver.async_media"],
        )
        if not file:
            return None

        info, name = _parse_document_meta(evt, file, attrs, thumb_size)

        event_type = EventType.ROOM_MESSAGE
        # Elements only support images as stickers, so send animated webm stickers as m.video
        if attrs.is_sticker and file.mime_type.startswith("image/"):
            event_type = EventType.STICKER
            # Tell clients to render the stickers as 256x256 if they're bigger
            if info.width > 256 or info.height > 256:
                if info.width > info.height:
                    info.height = int(info.height / (info.width / 256))
                    info.width = 256
                else:
                    info.width = int(info.width / (info.height / 256))
                    info.height = 256
            if info.thumbnail_info:
                info.thumbnail_info.width = info.width
                info.thumbnail_info.height = info.height
        if attrs.is_gif or (attrs.is_sticker and info.mimetype == "video/webm"):
            if attrs.is_gif:
                info["fi.mau.telegram.gif"] = True
            else:
                info["fi.mau.telegram.animated_sticker"] = True
            info["fi.mau.loop"] = True
            info["fi.mau.autoplay"] = True
            info["fi.mau.hide_controls"] = True
            info["fi.mau.no_audio"] = True
        if not name:
            ext = sane_mimetypes.guess_extension(file.mime_type)
            name = "unnamed_file" + ext

        content = MediaMessageEventContent(
            body=name,
            info=info,
            msgtype={
                "video/": MessageType.VIDEO,
                "audio/": MessageType.AUDIO,
                "image/": MessageType.IMAGE,
            }.get(info.mimetype[:6], MessageType.FILE),
        )
        if event_type == EventType.STICKER:
            content.msgtype = None
        if attrs.is_audio:
            content["org.matrix.msc1767.audio"] = {"duration": attrs.duration * 1000}
            if attrs.waveform:
                content["org.matrix.msc1767.audio"]["waveform"] = [x << 5 for x in attrs.waveform]
            if attrs.is_voice:
                content["org.matrix.msc3245.voice"] = {}
        if file.decryption_info:
            content.file = file.decryption_info
        else:
            content.url = file.mxc

        caption_content = await formatter.telegram_to_matrix(evt, source) if evt.message else None

        return ConvertedMessage(
            type=event_type,
            content=content,
            caption=caption_content,
            disappear_seconds=evt.media.ttl_seconds,
        )

    @staticmethod
    async def _convert_location(evt: Message, **_) -> ConvertedMessage:
        long = evt.media.geo.long
        lat = evt.media.geo.lat
        long_char = "E" if long > 0 else "W"
        lat_char = "N" if lat > 0 else "S"
        geo = f"{round(lat, 6)},{round(long, 6)}"

        body = f"{round(abs(lat), 4)}° {lat_char}, {round(abs(long), 4)}° {long_char}"
        url = f"https://maps.google.com/?q={geo}"

        if isinstance(evt.media, MessageMediaGeoLive):
            note = "Live Location (see your Telegram client for live updates)"
        elif isinstance(evt.media, MessageMediaVenue):
            note = evt.media.title
        else:
            note = "Location"

        content = LocationMessageEventContent(
            msgtype=MessageType.LOCATION,
            geo_uri=f"geo:{geo}",
            body=f"{note}: {body}\n{url}",
        )
        content["format"] = str(Format.HTML)
        content["formatted_body"] = f"{note}: <a href='{url}'>{body}</a>"
        content["org.matrix.msc3488.location"] = {
            "uri": content.geo_uri,
            "description": note,
        }
        return ConvertedMessage(content=content)

    @staticmethod
    async def _convert_unsupported(source: au.AbstractUser, evt: Message, **_) -> ConvertedMessage:
        override_text = (
            "This message is not supported on your version of Mautrix-Telegram. "
            "Please check https://github.com/mautrix/telegram or ask your "
            "bridge administrator about possible updates."
        )
        content = await formatter.telegram_to_matrix(evt, source, override_text=override_text)
        content.msgtype = MessageType.NOTICE
        content["fi.mau.telegram.unsupported"] = True
        return ConvertedMessage(content=content)

    async def _convert_poll(self, source: au.AbstractUser, evt: Message, **_) -> ConvertedMessage:
        poll: Poll = evt.media.poll
        poll_id = self._encode_msgid(source, evt)

        _n = 0

        def n() -> int:
            nonlocal _n
            _n += 1
            return _n

        text_answers = "\n".join(f"{n()}. {answer.text}" for answer in poll.answers)
        html_answers = "\n".join(f"<li>{answer.text}</li>" for answer in poll.answers)
        vote_command = f"{self.command_prefix} vote {poll_id}"
        content = TextMessageEventContent(
            msgtype=MessageType.TEXT,
            format=Format.HTML,
            body=(
                f"Poll: {poll.question}\n{text_answers}\n"
                f"Vote with {vote_command} <choice number>"
            ),
            formatted_body=(
                f"<strong>Poll</strong>: {poll.question}<br/>\n"
                f"<ol>{html_answers}</ol>\n"
                f"Vote with <code>{vote_command} &lt;choice number&gt;</code>"
            ),
        )

        return ConvertedMessage(content=content)

    @staticmethod
    async def _convert_dice(evt: Message, **_) -> ConvertedMessage:
        roll: MessageMediaDice = evt.media
        emoji_text = {
            "\U0001F3AF": " Dart throw",
            "\U0001F3B2": " Dice roll",
            "\U0001F3C0": " Basketball throw",
            "\U0001F3B0": " Slot machine",
            "\U0001F3B3": " Bowling",
            "\u26BD": " Football kick",
        }
        text = f"{roll.emoticon}{emoji_text.get(roll.emoticon, '')} result: {_format_dice(roll)}"
        content = TextMessageEventContent(
            msgtype=MessageType.TEXT,
            format=Format.HTML,
            body=text,
            formatted_body=f"<h4>{text}</h4>",
        )
        content["fi.mau.telegram.dice"] = {"emoticon": roll.emoticon, "value": roll.value}
        return ConvertedMessage(content=content)

    async def _convert_game(self, source: au.AbstractUser, evt: Message, **_) -> ConvertedMessage:
        game: Game = evt.media.game
        play_id = self._encode_msgid(source, evt)
        command = f"{self.command_prefix} play {play_id}"
        override_text = f"Run {command} in your bridge management room to play {game.title}"
        override_entities = [
            MessageEntityPre(offset=len("Run "), length=len(command), language="")
        ]

        content = await formatter.telegram_to_matrix(
            evt, source, override_text=override_text, override_entities=override_entities
        )
        content.msgtype = MessageType.NOTICE
        content["fi.mau.telegram.game"] = play_id

        return ConvertedMessage(content=content)

    @staticmethod
    async def _convert_contact(source: au.AbstractUser, evt: Message, **_) -> ConvertedMessage:
        contact: MessageMediaContact = evt.media
        name = " ".join(x for x in [contact.first_name, contact.last_name] if x)
        formatted_phone = f"+{contact.phone_number}"
        if phonenumbers is not None:
            try:
                parsed = phonenumbers.parse(formatted_phone)
                fmt = phonenumbers.PhoneNumberFormat.INTERNATIONAL
                formatted_phone = phonenumbers.format_number(parsed, fmt)
            except phonenumbers.NumberParseException:
                pass
        content = TextMessageEventContent(
            msgtype=MessageType.TEXT,
            body=f"Shared contact info for {name}: {formatted_phone}",
        )
        content["fi.mau.telegram.contact"] = {
            "user_id": contact.user_id,
            "first_name": contact.first_name,
            "last_name": contact.last_name,
            "phone_number": contact.phone_number,
            "vcard": contact.vcard,
        }

        puppet = await pu.Puppet.get_by_tgid(TelegramID(contact.user_id))
        if not puppet.displayname:
            try:
                entity = await source.client.get_entity(PeerUser(contact.user_id))
                await puppet.update_info(source, entity)
            except Exception as e:
                source.log.warning(f"Failed to sync puppet info of received contact: {e}")
        else:
            content.format = Format.HTML
            content.formatted_body = (
                f"Shared contact info for "
                f"<a href='https://matrix.to/#/{puppet.mxid}'>{html.escape(name)}</a>: "
                f"{html.escape(formatted_phone)}"
            )
        return ConvertedMessage(content=content)


def _parse_document_attributes(attributes: list[TypeDocumentAttribute]) -> DocAttrs:
    name, mime_type, is_sticker, sticker_alt, width, height = None, None, False, None, 0, 0
    is_gif, is_audio, is_voice, duration, waveform = False, False, False, 0, bytes()
    for attr in attributes:
        if isinstance(attr, DocumentAttributeFilename):
            name = name or attr.file_name
            mime_type, _ = mimetypes.guess_type(attr.file_name)
        elif isinstance(attr, DocumentAttributeSticker):
            is_sticker = True
            sticker_alt = attr.alt
        elif isinstance(attr, DocumentAttributeAnimated):
            is_gif = True
        elif isinstance(attr, DocumentAttributeVideo):
            width, height = attr.w, attr.h
        elif isinstance(attr, DocumentAttributeImageSize):
            width, height = attr.w, attr.h
        elif isinstance(attr, DocumentAttributeAudio):
            is_audio = True
            is_voice = attr.voice or False
            duration = attr.duration
            waveform = decode_waveform(attr.waveform) if attr.waveform else b""

    return DocAttrs(
        name,
        mime_type,
        is_sticker,
        sticker_alt,
        width,
        height,
        is_gif,
        is_audio,
        is_voice,
        duration,
        waveform,
    )


def _parse_document_meta(
    evt: Message, file: DBTelegramFile, attrs: DocAttrs, thumb_size: TypePhotoSize
) -> tuple[ImageInfo, str]:
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
    elif file.mime_type == "application/ogg":
        mime_type = "audio/ogg"
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
        info.thumbnail_info = ThumbnailInfo(
            mimetype=file.thumbnail.mime_type,
            height=file.thumbnail.height or thumb_size.h,
            width=file.thumbnail.width or thumb_size.w,
            size=file.thumbnail.size,
        )
    elif attrs.is_sticker:
        # This is a hack for bad clients like Element iOS that require a thumbnail
        info.thumbnail_info = ImageInfo.deserialize(info.serialize())
        if file.decryption_info:
            info.thumbnail_file = file.decryption_info
        else:
            info.thumbnail_url = file.mxc

    return info, name


def _format_dice(roll: MessageMediaDice) -> str:
    if roll.emoticon == "\U0001F3B0":
        emojis = {
            0: "\U0001F36B",  # "🍫",
            1: "\U0001F352",  # "🍒",
            2: "\U0001F34B",  # "🍋",
            3: "7\ufe0f\u20e3",  # "7️⃣",
        }
        res = roll.value - 1
        slot1, slot2, slot3 = emojis[res % 4], emojis[res // 4 % 4], emojis[res // 16]
        return f"{slot1} {slot2} {slot3} ({roll.value})"
    elif roll.emoticon == "\u26BD":
        results = {
            1: "miss",
            2: "hit the woodwork",
            3: "goal",  # seems to go in through the center
            4: "goal",
            5: "goal 🎉",  # seems to go in through the top right corner, includes confetti
        }
    elif roll.emoticon == "\U0001F3B3":
        results = {
            1: "miss",
            2: "1 pin down",
            3: "3 pins down, split",
            4: "4 pins down, split",
            5: "5 pins down",
            6: "strike 🎉",
        }
    # elif roll.emoticon == "\U0001F3C0":
    #     results = {
    #         2: "rolled off",
    #         3: "stuck",
    #     }
    # elif roll.emoticon == "\U0001F3AF":
    #     results = {
    #         1: "bounced off",
    #         2: "outer rim",
    #
    #         6: "bullseye",
    #     }
    else:
        return str(roll.value)
    return f"{results[roll.value]} ({roll.value})"
