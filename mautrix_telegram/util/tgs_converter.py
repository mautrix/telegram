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
import tempfile

from attr import dataclass

log: logging.Logger = logging.getLogger("mau.util.tgs")


@dataclass
class ConvertedSticker:
    mime: str
    data: bytes
    thumbnail_mime: Optional[str] = None
    thumbnail_data: Optional[bytes] = None
    width: int = 0
    height: int = 0


Converter = Callable[[bytes, int, int, Any], Awaitable[ConvertedSticker]]
converters: Dict[str, Converter] = {}


def abswhich(program: Optional[str]) -> Optional[str]:
    path = shutil.which(program)
    return os.path.abspath(path) if path else None


lottieconverter = abswhich("lottieconverter")
ffmpeg = abswhich("ffmpeg")

if lottieconverter:
    async def tgs_to_png(file: bytes, width: int, height: int, **_: Any) -> ConvertedSticker:
        frame = 1
        proc = await asyncio.create_subprocess_exec(lottieconverter, "-", "-", "png",
                                                    f"{width}x{height}", str(frame),
                                                    stdout=asyncio.subprocess.PIPE,
                                                    stdin=asyncio.subprocess.PIPE)
        stdout, stderr = await proc.communicate(file)
        if proc.returncode == 0:
            return ConvertedSticker("image/png", stdout)
        else:
            log.error("lottieconverter error: " + (stderr.decode("utf-8") if stderr is not None
                                                   else f"unknown ({proc.returncode})"))
            return ConvertedSticker("application/gzip", file)


    async def tgs_to_gif(file: bytes, width: int, height: int, background: str = "202020",
                         **_: Any) -> ConvertedSticker:
        proc = await asyncio.create_subprocess_exec(lottieconverter, "-", "-", "gif",
                                                    f"{width}x{height}", f"0x{background}",
                                                    stdout=asyncio.subprocess.PIPE,
                                                    stdin=asyncio.subprocess.PIPE)
        stdout, stderr = await proc.communicate(file)
        if proc.returncode == 0:
            return ConvertedSticker("image/gif", stdout)
        else:
            log.error("lottieconverter error: " + (stderr.decode("utf-8") if stderr is not None
                                                   else f"unknown ({proc.returncode})"))
            return ConvertedSticker("application/gzip", file)


    converters["png"] = tgs_to_png
    converters["gif"] = tgs_to_gif

if lottieconverter and ffmpeg:
    async def tgs_to_webm(file: bytes, width: int, height: int, fps: int = 30,
                          **_: Any) -> ConvertedSticker:
        with tempfile.TemporaryDirectory(prefix="tgs_") as tmpdir:
            file_template = tmpdir + "/out_"
            proc = await asyncio.create_subprocess_exec(lottieconverter, "-", file_template,
                                                        "pngs", f"{width}x{height}", str(fps),
                                                        stdout=asyncio.subprocess.PIPE,
                                                        stdin=asyncio.subprocess.PIPE)
            _, stderr = await proc.communicate(file)
            if proc.returncode == 0:
                with open(f"{file_template}00.png", "rb") as first_frame_file:
                    first_frame_data = first_frame_file.read()
                proc = await asyncio.create_subprocess_exec(ffmpeg, "-hide_banner", "-loglevel",
                                                            "error", "-framerate", str(fps),
                                                            "-pattern_type", "glob", "-i",
                                                            file_template + "*.png",
                                                            "-c:v", "libvpx-vp9", "-pix_fmt",
                                                            "yuva420p", "-f", "webm", "-",
                                                            stdout=asyncio.subprocess.PIPE,
                                                            stdin=asyncio.subprocess.PIPE)
                stdout, stderr = await proc.communicate()
                if proc.returncode == 0:
                    return ConvertedSticker("video/webm", stdout, "image/png", first_frame_data)
                else:
                    log.error("ffmpeg error: " + (stderr.decode("utf-8") if stderr is not None
                                                  else f"unknown ({proc.returncode})"))
            else:
                log.error("lottieconverter error: " + (stderr.decode("utf-8") if stderr is not None
                                                       else f"unknown ({proc.returncode})"))
        return ConvertedSticker("application/gzip", file)


    converters["webm"] = tgs_to_webm


async def convert_tgs_to(file: bytes, convert_to: str, width: int, height: int, **kwargs: Any
                         ) -> ConvertedSticker:
    if convert_to in converters:
        converter = converters[convert_to]
        converted = await converter(file, width, height, **kwargs)
        converted.width = width
        converted.height = height
        return converted
    elif convert_to != "disable":
        log.warning(f"Unable to convert animated sticker, type {convert_to} not supported")
    return ConvertedSticker("application/gzip", file)
