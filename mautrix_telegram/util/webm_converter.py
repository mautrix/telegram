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

import logging

from mautrix.util import ffmpeg

from .tgs_converter import ConvertedSticker

log: logging.Logger = logging.getLogger("mau.util.webm")


converter_args = {
    "gif": {
        "output_args": ("-vf", "split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"),
    },
    "png": {
        "input_args": ("-ss", "0"),
        "output_args": ("-frames:v", "1"),
    },
    "webp": {},
}


async def convert_webm_to(file: bytes, convert_to: str) -> ConvertedSticker:
    if convert_to in ("png", "gif", "webp"):
        try:
            converted_data = await ffmpeg.convert_bytes(
                data=file,
                output_extension=f".{convert_to}",
                **converter_args[convert_to],
            )
            return ConvertedSticker(f"image/{convert_to}", converted_data)
        except ffmpeg.ConverterError as e:
            log.error(str(e))
    elif convert_to != "disable":
        log.warning(f"Unable to convert webm animated sticker, type {convert_to} not supported")
    return ConvertedSticker("video/webm", file)
