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
import time
import logging

import magic
from PIL import Image
from sqlalchemy.exc import IntegrityError

from telethon.tl.types import (Document, FileLocation, InputFileLocation,
                               InputDocumentFileLocation)
from telethon.errors import LocationInvalidError

from ..db import TelegramFile as DBTelegramFile

log = logging.getLogger("mau.util")


def _convert_webp(file, to="png"):
    try:
        image = Image.open(BytesIO(file)).convert("RGBA")
        new_file = BytesIO()
        image.save(new_file, to)
        return f"image/{to}", new_file.getvalue()
    except Exception:
        log.exception(f"Failed to convert webp to {to}")
        return "image/webp", file


async def transfer_file_to_matrix(db, client, intent, location):
    if isinstance(location, (Document, InputDocumentFileLocation)):
        id = f"{location.id}-{location.version}"
    elif isinstance(location, (FileLocation, InputFileLocation)):
        id = f"{location.volume_id}-{location.local_id}"
    else:
        return None

    db_file = DBTelegramFile.query.get(id)
    if db_file:
        return db_file

    try:
        file = await client.download_file_bytes(location)
    except LocationInvalidError:
        return None
    mime_type = magic.from_buffer(file, mime=True)

    image_converted = False
    if mime_type == "image/webp":
        mime_type, file = _convert_webp(file, to="png")
        image_converted = True

    uploaded = await intent.upload_file(file, mime_type)

    db_file = DBTelegramFile(id=id, mxc=uploaded["content_uri"],
                             mime_type=mime_type, was_converted=image_converted,
                             timestamp=int(time.time()))
    try:
        db.add(db_file)
        db.commit()
    except IntegrityError:
        log.exception("Integrity error while saving transferred file data. "
                      "This was probably caused by two simultaneous transfers of the same file, "
                      "and should not cause any problems.")

    return db_file
