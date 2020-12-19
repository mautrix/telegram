# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Telegram lottie sticker converter
# Copyright (C) 2019 Randall Eramde Lawrence
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
from typing import Optional, Any
import concurrent.futures
import mimetypes
import asyncio
import logging
import json
import gzip
import io

from attr import dataclass
import magic

try:
    from lottie.objects import Animation
    from lottie.exporters import exporters
    from lottie.parsers.baseporter import Baseporter
except ImportError:
    Animation = None
    Baseporter = None
    exporters = None

log: logging.Logger = logging.getLogger("mau.util.tgs")
pool = concurrent.futures.ThreadPoolExecutor()


@dataclass
class ConvertedSticker:
    mime: str
    data: bytes
    thumbnail_mime: Optional[str] = None
    thumbnail_data: Optional[bytes] = None
    width: int = 0
    height: int = 0


def _convert_tgs(anim: Animation, exporter: Baseporter, kwargs: Any) -> bytes:
    out = io.BytesIO()
    exporter.process(anim, out, **kwargs)
    return out.getvalue()


async def convert_tgs_to(file: bytes, convert_to: str, width: int, height: int,
                         thumbnail: bool = False, **kwargs: Any) -> ConvertedSticker:
    loop = asyncio.get_running_loop()
    data = json.loads(gzip.decompress(file))
    anim: Animation = Animation.load(data)
    anim.width = width
    anim.height = height

    if convert_to in exporters.items:
        exporter: Baseporter = exporters.items[convert_to]
        data = await loop.run_in_executor(pool, _convert_tgs,
                                          anim, exporter, kwargs)
        mime = (mimetypes.guess_type(f"a.{exporter.extensions[0]}")[0]
                if len(exporter.extensions) == 1 else None)
        if not mime:
            mime = magic.from_buffer(data, mime=True)
        if not mime:
            mime = "image/webp"

        converted = ConvertedSticker(mime, data)
        converted.width = width
        converted.height = height
        if thumbnail and "png" in exporters.items:
            converted.thumbnail_data = await loop.run_in_executor(pool, _convert_tgs,
                                                                  anim, exporters.items["png"])
            converted.thumbnail_mime = "image/png"
        return converted
    elif convert_to != "disable":
        log.warning(f"Unable to convert animated sticker, type {convert_to} not supported")
    return ConvertedSticker("image/x-lottie+json", json.dumps(data).encode("utf-8"))
