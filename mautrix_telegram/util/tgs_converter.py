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
from typing import Dict, Callable, Awaitable, Optional, Tuple, Any
import asyncio.subprocess
import logging
import shutil
import os.path

log: logging.Logger = logging.getLogger("mau.util.tgs")
converters: Dict[str, Callable[[bytes, int, int, Any], Awaitable[Tuple[str, bytes]]]] = {}


def abswhich(program: Optional[str]) -> Optional[str]:
    path = shutil.which(program)
    return os.path.abspath(path) if path else None


lottieconverter = abswhich("lottieconverter")
lottie2ffmpeg = abswhich("lottie2ffmpeg")

if lottieconverter:
    async def tgs_to_png(file: bytes, width: int, height: int, **_: Any) -> Tuple[str, bytes]:
        frame = 1
        proc = await asyncio.create_subprocess_exec(lottieconverter, "-", "-", "png",
                                                    f"{width}x{height}", str(frame),
                                                    stdout=asyncio.subprocess.PIPE,
                                                    stdin=asyncio.subprocess.PIPE)
        stdout, _ = await proc.communicate(file)
        return "image/png", stdout


    async def tgs_to_gif(file: bytes, width: int, height: int, background: str = "202020",
                         **_: Any) -> Tuple[str, bytes]:
        proc = await asyncio.create_subprocess_exec(lottieconverter, "-", "-", "gif",
                                                    f"{width}x{height}", "0", f"0x{background}",
                                                    stdout=asyncio.subprocess.PIPE,
                                                    stdin=asyncio.subprocess.PIPE)
        stdout, _ = await proc.communicate(file)
        return "image/gif", stdout


    converters["png"] = tgs_to_png
    converters["gif"] = tgs_to_gif

if lottieconverter and lottie2ffmpeg:
    async def tgs_to_webm(file: bytes, width: int, height: int, **_: Any) -> Tuple[str, bytes]:
        proc = await asyncio.create_subprocess_exec(lottie2ffmpeg, lottieconverter,
                                                    f"{width}x{height}",
                                                    stdout=asyncio.subprocess.PIPE,
                                                    stdin=asyncio.subprocess.PIPE)
        stdout, _ = await proc.communicate(file)
        return "video/webm", stdout


    converters["webm"] = tgs_to_webm


async def convert_tgs_to(file: bytes, convert_to: str, width: int, height: int, **kwargs: Any
                         ) -> Tuple[str, bytes, Optional[int], Optional[int], Optional[bytes]]:
    if convert_to in converters:
        converter = converters[convert_to]
        mime, out = await converter(file, width, height, **kwargs)
        return mime, out, width, height, None
    elif convert_to != "disable":
        log.warning(f"Unable to convert animated sticker, type {convert_to} not supported")
    return "application/gzip", file, None, None, None
