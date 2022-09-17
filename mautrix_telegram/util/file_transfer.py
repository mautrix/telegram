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

from typing import Optional, Union
from io import BytesIO
from sqlite3 import IntegrityError
import asyncio
import logging
import tempfile
import time

from asyncpg import UniqueViolationError
from telethon.errors import (
    AuthBytesInvalidError,
    AuthKeyInvalidError,
    FileIdInvalidError,
    LocationInvalidError,
    SecurityError,
)
from telethon.tl.functions.messages import GetCustomEmojiDocumentsRequest
from telethon.tl.types import (
    Document,
    InputDocumentFileLocation,
    InputFileLocation,
    InputPeerPhotoFileLocation,
    InputPhotoFileLocation,
    PhotoCachedSize,
    PhotoSize,
    TypePhotoSize,
)

from mautrix.appservice import IntentAPI
from mautrix.util import magic

from .. import abstract_user as au
from ..db import TelegramFile as DBTelegramFile
from ..tgclient import MautrixTelegramClient
from ..util import sane_mimetypes
from .parallel_file_transfer import parallel_transfer_to_matrix
from .tgs_converter import convert_tgs_to
from .webm_converter import convert_webm_to

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    from moviepy.editor import VideoFileClip
except ImportError:
    VideoFileClip = None

try:
    from mautrix.crypto.attachments import encrypt_attachment
except ImportError:
    encrypt_attachment = None

log: logging.Logger = logging.getLogger("mau.util")

TypeLocation = Union[
    Document,
    InputDocumentFileLocation,
    InputPeerPhotoFileLocation,
    InputFileLocation,
    InputPhotoFileLocation,
]


def convert_image(
    file: bytes,
    source_mime: str = "image/webp",
    target_type: str = "png",
    thumbnail_to: tuple[int, int] | None = None,
) -> tuple[str, bytes, int | None, int | None]:
    if not Image:
        return source_mime, file, None, None
    try:
        image: Image.Image = Image.open(BytesIO(file)).convert("RGBA")
        if thumbnail_to:
            image.thumbnail(thumbnail_to, Image.ANTIALIAS)
        new_file = BytesIO()
        image.save(new_file, target_type)
        w, h = image.size
        return f"image/{target_type}", new_file.getvalue(), w, h
    except Exception:
        log.exception(f"Failed to convert {source_mime} to {target_type}")
        return source_mime, file, None, None


def _read_video_thumbnail(
    data: bytes,
    video_ext: str = "mp4",
    frame_ext: str = "png",
    max_size: tuple[int, int] = (1024, 720),
) -> tuple[bytes, int, int]:
    with tempfile.NamedTemporaryFile(prefix="mxtg_video_", suffix=f".{video_ext}") as file:
        # We don't have any way to read the video from memory, so save it to disk.
        file.write(data)

        # Read temp file and get frame
        frame = VideoFileClip(file.name).get_frame(0)

    # Convert to png and save to BytesIO
    image = Image.fromarray(frame).convert("RGBA")

    thumbnail_file = BytesIO()
    if max_size:
        image.thumbnail(max_size, Image.ANTIALIAS)
    image.save(thumbnail_file, frame_ext)

    w, h = image.size
    return thumbnail_file.getvalue(), w, h


def _location_to_id(location: TypeLocation) -> str:
    if isinstance(location, Document):
        return str(location.id)
    elif isinstance(location, (InputDocumentFileLocation, InputPhotoFileLocation)):
        return f"{location.id}-{location.thumb_size}"
    elif isinstance(location, InputFileLocation):
        return f"{location.volume_id}-{location.local_id}"
    elif isinstance(location, InputPeerPhotoFileLocation):
        return str(location.photo_id)


async def transfer_thumbnail_to_matrix(
    client: MautrixTelegramClient,
    intent: IntentAPI,
    thumbnail_loc: TypeLocation,
    mime_type: str,
    encrypt: bool,
    video: bytes | None,
    custom_data: bytes | None = None,
    width: int | None = None,
    height: int | None = None,
    async_upload: bool = False,
) -> DBTelegramFile | None:
    if not Image or not VideoFileClip:
        return None

    loc_id = _location_to_id(thumbnail_loc)
    if not loc_id:
        return None

    if custom_data:
        loc_id += "-mau_custom_thumbnail"
    if encrypt:
        loc_id += "-encrypted"

    db_file = await DBTelegramFile.get(loc_id)
    if db_file:
        return db_file

    video_ext = sane_mimetypes.guess_extension(mime_type)
    if custom_data:
        file = custom_data
    elif VideoFileClip and video_ext and video:
        try:
            file, width, height = _read_video_thumbnail(video, video_ext, frame_ext="png")
        except OSError:
            return None
        mime_type = "image/png"
    else:
        file = await client.download_file(thumbnail_loc)
        width, height = None, None
        mime_type = magic.mimetype(file)

    decryption_info = None
    upload_mime_type = mime_type
    if encrypt:
        file, decryption_info = encrypt_attachment(file)
        upload_mime_type = "application/octet-stream"
    content_uri = await intent.upload_media(file, upload_mime_type, async_upload=async_upload)
    if decryption_info:
        decryption_info.url = content_uri

    db_file = DBTelegramFile(
        id=loc_id,
        mxc=content_uri,
        mime_type=mime_type,
        was_converted=False,
        timestamp=int(time.time()),
        size=len(file),
        width=width,
        height=height,
        decryption_info=decryption_info,
    )
    try:
        await db_file.insert()
    except (UniqueViolationError, IntegrityError) as e:
        log.exception(
            f"{e.__class__.__name__} while saving transferred file thumbnail data. "
            "This was probably caused by two simultaneous transfers of the same file, "
            "and might (but probably won't) cause problems with thumbnails or something."
        )
    return db_file


transfer_locks: dict[str, asyncio.Lock] = {}

TypeThumbnail = Optional[Union[TypeLocation, TypePhotoSize]]


async def transfer_custom_emojis_to_matrix(
    source: au.AbstractUser, emoji_ids: list[int]
) -> dict[int, DBTelegramFile]:
    emoji_ids = set(emoji_ids)
    existing = await DBTelegramFile.get_many([str(id) for id in emoji_ids])
    file_map = {int(file.id): file for file in existing}
    not_existing_ids = list(emoji_ids - file_map.keys())
    if not_existing_ids:
        log.debug(f"Transferring custom emojis through {source.mxid}: {not_existing_ids}")

        documents: list[Document] = await source.client(
            GetCustomEmojiDocumentsRequest(document_id=not_existing_ids)
        )

        tgs_args = source.config["bridge.animated_emoji"]
        webm_convert = tgs_args["target"]

        transfer_sema = asyncio.Semaphore(5)

        async def transfer(document: Document) -> None:
            async with transfer_sema:
                file_map[document.id] = await transfer_file_to_matrix(
                    source.client,
                    source.bridge.az.intent,
                    document,
                    is_sticker=True,
                    tgs_convert=tgs_args,
                    webm_convert=webm_convert,
                    filename=f"emoji-{document.id}",
                    # Emojis are used as inline images and can't be encrypted
                    encrypt=False,
                    async_upload=source.config["homeserver.async_media"],
                )

        await asyncio.gather(*[transfer(doc) for doc in documents])
    return file_map


async def transfer_file_to_matrix(
    client: MautrixTelegramClient,
    intent: IntentAPI,
    location: TypeLocation,
    thumbnail: TypeThumbnail = None,
    *,
    is_sticker: bool = False,
    tgs_convert: dict | None = None,
    webm_convert: str | None = None,
    filename: str | None = None,
    encrypt: bool = False,
    parallel_id: int | None = None,
    async_upload: bool = False,
) -> DBTelegramFile | None:
    location_id = _location_to_id(location)
    if not location_id:
        return None
    if encrypt:
        location_id += "-encrypted"

    db_file = await DBTelegramFile.get(location_id)
    if db_file:
        return db_file

    try:
        lock = transfer_locks[location_id]
    except KeyError:
        lock = asyncio.Lock()
        transfer_locks[location_id] = lock
    async with lock:
        return await _unlocked_transfer_file_to_matrix(
            client,
            intent,
            location_id,
            location,
            thumbnail,
            is_sticker,
            tgs_convert,
            webm_convert,
            filename,
            encrypt,
            parallel_id,
            async_upload=async_upload,
        )


async def _unlocked_transfer_file_to_matrix(
    client: MautrixTelegramClient,
    intent: IntentAPI,
    loc_id: str,
    location: TypeLocation,
    thumbnail: TypeThumbnail,
    is_sticker: bool,
    tgs_convert: dict | None,
    webm_convert: str | None,
    filename: str | None,
    encrypt: bool,
    parallel_id: int | None,
    async_upload: bool = False,
) -> DBTelegramFile | None:
    db_file = await DBTelegramFile.get(loc_id)
    if db_file:
        return db_file

    converted_anim = None

    if parallel_id and isinstance(location, Document) and (not is_sticker or not tgs_convert):
        db_file = await parallel_transfer_to_matrix(
            client, intent, loc_id, location, filename, encrypt, parallel_id
        )
        mime_type = location.mime_type
        file = None
    else:
        try:
            file = await client.download_file(location)
        except (LocationInvalidError, FileIdInvalidError):
            return None
        except (AuthBytesInvalidError, AuthKeyInvalidError, SecurityError) as e:
            log.exception(f"{e.__class__.__name__} while downloading a file.")
            return None

        width, height = None, None
        mime_type = magic.mimetype(file)

        image_converted = False
        is_tgs = mime_type == "application/gzip"
        if is_sticker and tgs_convert and is_tgs:
            converted_anim = await convert_tgs_to(
                file, tgs_convert["target"], **tgs_convert["args"]
            )
            mime_type = converted_anim.mime
            file = converted_anim.data
            width, height = converted_anim.width, converted_anim.height
            image_converted = mime_type != "application/gzip"
            thumbnail = None
        elif is_sticker and webm_convert and webm_convert != "webm" and mime_type == "video/webm":
            converted_anim = await convert_webm_to(file, webm_convert)
            mime_type = converted_anim.mime
            file = converted_anim.data
            image_converted = mime_type != "video/webm"
            thumbnail = None

        decryption_info = None
        upload_mime_type = mime_type
        if encrypt and encrypt_attachment:
            file, decryption_info = encrypt_attachment(file)
            upload_mime_type = "application/octet-stream"
        content_uri = await intent.upload_media(file, upload_mime_type, async_upload=async_upload)
        if decryption_info:
            decryption_info.url = content_uri

        db_file = DBTelegramFile(
            id=loc_id,
            mxc=content_uri,
            decryption_info=decryption_info,
            mime_type=mime_type,
            was_converted=image_converted,
            timestamp=int(time.time()),
            size=len(file),
            width=width,
            height=height,
        )
    if thumbnail and (mime_type.startswith("video/") or mime_type == "image/gif"):
        if isinstance(thumbnail, (PhotoSize, PhotoCachedSize)):
            thumbnail = thumbnail.location
        try:
            db_file.thumbnail = await transfer_thumbnail_to_matrix(
                client,
                intent,
                thumbnail,
                video=file,
                mime_type=mime_type,
                encrypt=encrypt,
                async_upload=async_upload,
            )
        except FileIdInvalidError:
            log.warning(f"Failed to transfer thumbnail for {thumbnail!s}", exc_info=True)
    elif converted_anim and converted_anim.thumbnail_data:
        db_file.thumbnail = await transfer_thumbnail_to_matrix(
            client,
            intent,
            location,
            video=None,
            encrypt=encrypt,
            custom_data=converted_anim.thumbnail_data,
            mime_type=converted_anim.thumbnail_mime,
            width=converted_anim.width,
            height=converted_anim.height,
            async_upload=async_upload,
        )

    try:
        await db_file.insert()
    except (UniqueViolationError, IntegrityError) as e:
        log.exception(
            f"{e.__class__.__name__} while saving transferred file data. "
            "This was probably caused by two simultaneous transfers of the same file, "
            "and should not cause any problems."
        )
    return db_file
