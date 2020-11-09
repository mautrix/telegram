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
from typing import Optional, Tuple, Union, Dict
from io import BytesIO
import time
import logging
import asyncio
import tempfile

import magic
from sqlalchemy.exc import IntegrityError, InvalidRequestError

from telethon.tl.types import (Document, InputFileLocation, InputDocumentFileLocation,
                               TypePhotoSize, PhotoSize, PhotoCachedSize, InputPhotoFileLocation,
                               InputPeerPhotoFileLocation)
from telethon.errors import (AuthBytesInvalidError, AuthKeyInvalidError, LocationInvalidError,
                             SecurityError, FileIdInvalidError)

from mautrix.appservice import IntentAPI
from mautrix.util.network_retry import call_with_net_retry

from ..tgclient import MautrixTelegramClient
from ..db import TelegramFile as DBTelegramFile
from ..util import sane_mimetypes
from .parallel_file_transfer import parallel_transfer_to_matrix
from .tgs_converter import convert_tgs_to

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

TypeLocation = Union[Document, InputDocumentFileLocation, InputPeerPhotoFileLocation,
                     InputFileLocation, InputPhotoFileLocation]


def convert_image(file: bytes, source_mime: str = "image/webp", target_type: str = "png",
                  thumbnail_to: Optional[Tuple[int, int]] = None
                  ) -> Tuple[str, bytes, Optional[int], Optional[int]]:
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


def _read_video_thumbnail(data: bytes, video_ext: str = "mp4", frame_ext: str = "png",
                          max_size: Tuple[int, int] = (1024, 720)) -> Tuple[bytes, int, int]:
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
        return f"{location.id}-{location.access_hash}"
    elif isinstance(location, (InputDocumentFileLocation, InputPhotoFileLocation)):
        return f"{location.id}-{location.access_hash}-{location.thumb_size}"
    elif isinstance(location, (InputFileLocation, InputPeerPhotoFileLocation)):
        return f"{location.volume_id}-{location.local_id}"


async def transfer_thumbnail_to_matrix(client: MautrixTelegramClient, intent: IntentAPI,
                                       thumbnail_loc: TypeLocation, mime_type: str, encrypt: bool,
                                       video: Optional[bytes], custom_data: Optional[bytes] = None,
                                       width: Optional[int] = None, height: [int] = None
                                       ) -> Optional[DBTelegramFile]:
    if not Image or not VideoFileClip:
        return None

    loc_id = _location_to_id(thumbnail_loc)
    if not loc_id:
        return None

    if custom_data:
        loc_id += "-mau_custom_thumbnail"

    db_file = DBTelegramFile.get(loc_id)
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
        mime_type = magic.from_buffer(file, mime=True)

    decryption_info = None
    upload_mime_type = mime_type
    if encrypt:
        file, decryption_info = encrypt_attachment(file)
        upload_mime_type = "application/octet-stream"
    content_uri = await call_with_net_retry(intent.upload_media, file, upload_mime_type,
                                            _action="upload media")
    if decryption_info:
        decryption_info.url = content_uri

    db_file = DBTelegramFile(id=loc_id, mxc=content_uri, mime_type=mime_type,
                             was_converted=False, timestamp=int(time.time()), size=len(file),
                             width=width, height=height, decryption_info=decryption_info)
    try:
        db_file.insert()
    except (IntegrityError, InvalidRequestError) as e:
        log.exception(f"{e.__class__.__name__} while saving transferred file thumbnail data. "
                      "This was probably caused by two simultaneous transfers of the same file, "
                      "and might (but probably won't) cause problems with thumbnails or something.")
    return db_file


transfer_locks: Dict[str, asyncio.Lock] = {}

TypeThumbnail = Optional[Union[TypeLocation, TypePhotoSize]]


async def transfer_file_to_matrix(client: MautrixTelegramClient, intent: IntentAPI,
                                  location: TypeLocation, thumbnail: TypeThumbnail = None, *,
                                  is_sticker: bool = False, tgs_convert: Optional[dict] = None,
                                  filename: Optional[str] = None, encrypt: bool = False,
                                  parallel_id: Optional[int] = None) -> Optional[DBTelegramFile]:
    location_id = _location_to_id(location)
    if not location_id:
        return None

    db_file = DBTelegramFile.get(location_id)
    if db_file:
        return db_file

    try:
        lock = transfer_locks[location_id]
    except KeyError:
        lock = asyncio.Lock()
        transfer_locks[location_id] = lock
    async with lock:
        return await _unlocked_transfer_file_to_matrix(client, intent, location_id, location,
                                                       thumbnail, is_sticker, tgs_convert,
                                                       filename, encrypt, parallel_id)


async def _unlocked_transfer_file_to_matrix(client: MautrixTelegramClient, intent: IntentAPI,
                                            loc_id: str, location: TypeLocation,
                                            thumbnail: TypeThumbnail, is_sticker: bool,
                                            tgs_convert: Optional[dict], filename: Optional[str],
                                            encrypt: bool, parallel_id: Optional[int]
                                            ) -> Optional[DBTelegramFile]:
    db_file = DBTelegramFile.get(loc_id)
    if db_file:
        return db_file

    converted_anim = None

    if parallel_id and isinstance(location, Document) and (not is_sticker or not tgs_convert):
        db_file = await parallel_transfer_to_matrix(client, intent, loc_id, location, filename,
                                                    encrypt, parallel_id)
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
        mime_type = magic.from_buffer(file, mime=True)

        image_converted = False
        # A weird bug in alpine/magic makes it return application/octet-stream for gzips...
        is_tgs = (mime_type == "application/gzip" or (mime_type == "application/octet-stream"
                                                      and magic.from_buffer(file).startswith(
                "gzip")))
        if is_sticker and tgs_convert and is_tgs:
            converted_anim = await convert_tgs_to(file, tgs_convert["target"],
                                                  **tgs_convert["args"])
            mime_type = converted_anim.mime
            file = converted_anim.data
            width, height = converted_anim.width, converted_anim.height
            image_converted = mime_type != "application/gzip"
            thumbnail = None

        if mime_type == "image/webp":
            new_mime_type, file, width, height = convert_image(
                file, source_mime="image/webp", target_type="png",
                thumbnail_to=(256, 256) if is_sticker else None)
            image_converted = new_mime_type != mime_type
            mime_type = new_mime_type
            thumbnail = None

        decryption_info = None
        upload_mime_type = mime_type
        if encrypt and encrypt_attachment:
            file, decryption_info = encrypt_attachment(file)
            upload_mime_type = "application/octet-stream"
        content_uri = await call_with_net_retry(intent.upload_media, file, upload_mime_type,
                                                _action="upload media")
        if decryption_info:
            decryption_info.url = content_uri

        db_file = DBTelegramFile(id=loc_id, mxc=content_uri, decryption_info=decryption_info,
                                 mime_type=mime_type, was_converted=image_converted,
                                 timestamp=int(time.time()), size=len(file),
                                 width=width, height=height)
    if thumbnail and (mime_type.startswith("video/") or mime_type == "image/gif"):
        if isinstance(thumbnail, (PhotoSize, PhotoCachedSize)):
            thumbnail = thumbnail.location
        try:
            db_file.thumbnail = await transfer_thumbnail_to_matrix(client, intent, thumbnail,
                                                                   video=file, mime_type=mime_type,
                                                                   encrypt=encrypt)
        except FileIdInvalidError:
            log.warning(f"Failed to transfer thumbnail for {thumbnail!s}", exc_info=True)
    elif converted_anim and converted_anim.thumbnail_data:
        db_file.thumbnail = await transfer_thumbnail_to_matrix(
            client, intent, location, video=None, encrypt=encrypt,
            custom_data=converted_anim.thumbnail_data, mime_type=converted_anim.thumbnail_mime,
            width=converted_anim.width, height=converted_anim.height)

    try:
        db_file.insert()
    except (IntegrityError, InvalidRequestError) as e:
        log.exception(f"{e.__class__.__name__} while saving transferred file data. "
                      "This was probably caused by two simultaneous transfers of the same file, "
                      "and should not cause any problems.")
    return db_file
