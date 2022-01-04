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
from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable
import asyncio.subprocess
import logging
import os.path
import shutil
import tempfile

from attr import dataclass

log: logging.Logger = logging.getLogger("mau.util.tgs")


@dataclass
class ConvertedSticker:
    mime: str
    data: bytes
    thumbnail_mime: str | None = None
    thumbnail_data: bytes | None = None
    width: int = 0
    height: int = 0


Converter = Callable[[bytes, int, int, Any], Awaitable[ConvertedSticker]]
converters: dict[str, Converter] = {}


def abswhich(program: str | None) -> str | None:
    path = shutil.which(program)
    return os.path.abspath(path) if path else None


lottieconverter = abswhich("lottieconverter")
ffmpeg = abswhich("ffmpeg")

if lottieconverter:

    async def tgs_to_png(file: bytes, width: int, height: int, **_: Any) -> ConvertedSticker:
        frame = 1
        proc = await asyncio.create_subprocess_exec(
            lottieconverter,
            "-",
            "-",
            "png",
            f"{width}x{height}",
            str(frame),
            stdout=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(file)
        if proc.returncode == 0:
            return ConvertedSticker("image/png", stdout)
        else:
            log.error(
                "lottieconverter error: "
                + (
                    stderr.decode("utf-8")
                    if stderr is not None
                    else f"unknown ({proc.returncode})"
                )
            )
            return ConvertedSticker("application/gzip", file)

    async def tgs_to_gif(
        file: bytes, width: int, height: int, fps: int = 25, **_: Any
    ) -> ConvertedSticker:
        proc = await asyncio.create_subprocess_exec(
            lottieconverter,
            "-",
            "-",
            "gif",
            f"{width}x{height}",
            str(fps),
            stdout=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(file)
        if proc.returncode == 0:
            return ConvertedSticker("image/gif", stdout)
        else:
            log.error(
                "lottieconverter error: "
                + (
                    stderr.decode("utf-8")
                    if stderr is not None
                    else f"unknown ({proc.returncode})"
                )
            )
            return ConvertedSticker("application/gzip", file)

    converters["png"] = tgs_to_png
    converters["gif"] = tgs_to_gif

if lottieconverter and ffmpeg:

    async def tgs_to_webm(
        file: bytes, width: int, height: int, fps: int = 30, **_: Any
    ) -> ConvertedSticker:
        with tempfile.TemporaryDirectory(prefix="tgs_") as tmpdir:
            file_template = tmpdir + "/out_"
            proc = await asyncio.create_subprocess_exec(
                lottieconverter,
                "-",
                file_template,
                "pngs",
                f"{width}x{height}",
                str(fps),
                stdout=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate(file)
            if proc.returncode == 0:
                first_frame = None
                for f in Path(tmpdir).glob("out_*0.png"):
                    if first_frame is None or first_frame.stem > f.stem:
                        first_frame = f

                if first_frame is not None:
                    proc = await asyncio.create_subprocess_exec(
                        ffmpeg,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-framerate",
                        str(fps),
                        "-pattern_type",
                        "glob",
                        "-i",
                        file_template + "*.png",
                        "-c:v",
                        "libvpx-vp9",
                        "-pix_fmt",
                        "yuva420p",
                        "-f",
                        "webm",
                        "-",
                        stdout=asyncio.subprocess.PIPE,
                        stdin=asyncio.subprocess.PIPE,
                    )
                    stdout, stderr = await proc.communicate()
                    if proc.returncode == 0:
                        return ConvertedSticker(
                            "video/webm", stdout, "image/png", first_frame.read_bytes()
                        )
                    else:
                        log.error(
                            "ffmpeg error: "
                            + (
                                stderr.decode("utf-8")
                                if stderr is not None
                                else f"unknown ({proc.returncode})"
                            )
                        )
                else:
                    log.error("lottieconverter error: unable to find output frames")
            else:
                log.error(
                    "lottieconverter error: "
                    + (
                        stderr.decode("utf-8")
                        if stderr is not None
                        else f"unknown ({proc.returncode})"
                    )
                )
        return ConvertedSticker("application/gzip", file)

    converters["webm"] = tgs_to_webm


async def convert_tgs_to(
    file: bytes, convert_to: str, width: int, height: int, **kwargs: Any
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
