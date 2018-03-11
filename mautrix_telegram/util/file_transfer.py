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

from telethon_aio.tl.types import (Document, FileLocation, InputFileLocation,
                                   InputDocumentFileLocation, PhotoSize, PhotoCachedSize)
from telethon_aio.errors import LocationInvalidError

from ..db import TelegramFile as DBTelegramFile

log = logging.getLogger("mau.util")


def _convert_webp(file, to="png"):
    if not Image:
        return "image/webp", file
    try:
        image = Image.open(BytesIO(file)).convert("RGBA")
        new_file = BytesIO()
        image.save(new_file, to)
        w, h = image.size
        return f"image/{to}", new_file.getvalue(), w, h
    except Exception:
        log.exception(f"Failed to convert webp to {to}")
        return "image/webp", file


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


async def transfer_file_to_matrix(db, client, intent, location, thumbnail=None):
    id = _location_to_id(location)
    if not id:
        return None

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
        mime_type, file, width, height = _convert_webp(file, to="png")
        thumbnail = None
        image_converted = True

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
