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
from io import BytesIO
import time
import logging
import asyncio

import magic
from sqlalchemy.exc import IntegrityError, InvalidRequestError
from sqlalchemy.orm.exc import FlushError

try:
    from PIL import Image
except ImportError:
    Image = None
try:
    from moviepy.editor import VideoFileClip
    import random
    import string
    import os
    import mimetypes
except ImportError:
    VideoFileClip = random = string = os = mimetypes = None

from telethon.tl.types import (Document, FileLocation, InputFileLocation,
                               InputDocumentFileLocation, PhotoSize, PhotoCachedSize)
from telethon.errors import LocationInvalidError

from ..db import TelegramFile as DBTelegramFile

log = logging.getLogger("mau.util")


def convert_image(file, source_mime="image/webp", target_type="png", thumbnail_to=None):
    if not Image:
        return source_mime, file, None, None
    try:
        image = Image.open(BytesIO(file)).convert("RGBA")
        if thumbnail_to:
            image.thumbnail(thumbnail_to, Image.ANTIALIAS)
        new_file = BytesIO()
        image.save(new_file, target_type)
        w, h = image.size
        return f"image/{target_type}", new_file.getvalue(), w, h
    except Exception:
        log.exception(f"Failed to convert {source_mime} to {target_type}")
        return source_mime, file, None, None


def _temp_file_name(ext):
    return ("/tmp/mxtg-video-"
            + "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(10))
            + ext)


def _read_video_thumbnail(data, video_ext="mp4", frame_ext="png", max_size=(1024, 720)):
    # We don't have any way to read the video from memory, so save it to disk.
    temp_file = _temp_file_name(video_ext)
    with open(temp_file, "wb") as file:
        file.write(data)

    # Read temp file and get frame
    clip = VideoFileClip(temp_file)
    frame = clip.get_frame(0)

    # Convert to png and save to BytesIO
    image = Image.fromarray(frame).convert("RGBA")
    thumbnail_file = BytesIO()
    if max_size:
        image.thumbnail(max_size, Image.ANTIALIAS)
    image.save(thumbnail_file, frame_ext)

    os.remove(temp_file)

    w, h = image.size
    return thumbnail_file.getvalue(), w, h


def _location_to_id(location):
    if isinstance(location, (Document, InputDocumentFileLocation)):
        return f"{location.id}-{location.version}"
    elif isinstance(location, (FileLocation, InputFileLocation)):
        return f"{location.volume_id}-{location.local_id}"
    else:
        return None


async def transfer_thumbnail_to_matrix(client, intent, thumbnail_loc, video, mime):
    if not Image or not VideoFileClip:
        return None

    id = _location_to_id(thumbnail_loc)
    if not id:
        return None

    video_ext = mimetypes.guess_extension(mime)
    if VideoFileClip and video_ext:
        try:
            file, width, height = _read_video_thumbnail(video, video_ext, frame_ext="png")
        except OSError:
            return None
        mime_type = "image/png"
    else:
        file = await client.download_file_bytes(thumbnail_loc)
        width, height = None, None
        mime_type = magic.from_buffer(file, mime=True)

    uploaded = await intent.upload_file(file, mime_type)

    return DBTelegramFile(id=id, mxc=uploaded["content_uri"], mime_type=mime_type,
                          was_converted=False, timestamp=int(time.time()), size=len(file),
                          width=width, height=height)


transfer_locks = {}
transfer_locks_lock = asyncio.Lock()


async def transfer_file_to_matrix(db, client, intent, location, thumbnail=None, is_sticker=False):
    id = _location_to_id(location)
    if not id:
        return None

    db_file = DBTelegramFile.query.get(id)
    if db_file:
        return db_file

    async with transfer_locks_lock:
        try:
            lock = transfer_locks[id]
        except KeyError:
            lock = asyncio.Lock()
            transfer_locks[id] = lock
    async with lock:
        return await _unlocked_transfer_file_to_matrix(db, client, intent, id, location, thumbnail, is_sticker)


async def _unlocked_transfer_file_to_matrix(db, client, intent, id, location, thumbnail, is_sticker):
    db_file = DBTelegramFile.query.get(id)
    if db_file:
        return db_file

    try:
        file = await client.download_file_bytes(location)
    except LocationInvalidError:
        return None
    width, height = None, None
    mime_type = magic.from_buffer(file, mime=True)

    image_converted = False
    if mime_type == "image/webp":
        new_mime_type, file, width, height = convert_image(file, source_mime="image/webp", target_type="png", thumbnail_to=(
            256, 256) if is_sticker else None)
        image_converted = new_mime_type != mime_type
        mime_type = new_mime_type
        thumbnail = None

    uploaded = await intent.upload_file(file, mime_type)

    db_file = DBTelegramFile(id=id, mxc=uploaded["content_uri"],
                             mime_type=mime_type, was_converted=image_converted,
                             timestamp=int(time.time()), size=len(file),
                             width=width, height=height)
    if thumbnail and (mime_type.startswith("video/") or mime_type == "image/gif"):
        if isinstance(thumbnail, (PhotoSize, PhotoCachedSize)):
            thumbnail = thumbnail.location
        db_file.thumbnail = await transfer_thumbnail_to_matrix(client, intent, thumbnail, file,
                                                               mime_type)

    try:
        db.add(db_file)
        db.commit()
    except FlushError as e:
        log.exception(f"{e.__class__.__name__} while saving transferred file data. "
                      "This was probably caused by two simultaneous transfers of the same file, "
                      "and should not cause any problems.")
    except (IntegrityError, InvalidRequestError) as e:
        db.rollback()
        log.exception(f"{e.__class__.__name__} while saving transferred file data. "
                      "This was probably caused by two simultaneous transfers of the same file, "
                      "and should not cause any problems.")

    return db_file
