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

from typing import Any, Awaitable, Callable
import asyncio.subprocess
import logging
import os
import os.path
import shutil
import tempfile

from attr import dataclass

from mautrix.util import ffmpeg

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


async def _run_lottieconverter(args: tuple[str, ...], input_data: bytes) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        lottieconverter,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate(input_data)
    if proc.returncode == 0:
        return stdout
    else:
        err_text = stderr.decode("utf-8") if stderr else f"unknown ({proc.returncode})"
        raise ffmpeg.ConverterError(f"lottieconverter error: {err_text}")


if lottieconverter:

    async def tgs_to_png(file: bytes, width: int, height: int, **_: Any) -> ConvertedSticker:
        frame = 1
        try:
            converted_png = await _run_lottieconverter(
                args=("-", "-", "png", f"{width}x{height}", str(frame)),
                input_data=file,
            )
            return ConvertedSticker("image/png", converted_png)
        except ffmpeg.ConverterError as e:
            log.error(str(e))
            return ConvertedSticker("application/gzip", file)

    async def tgs_to_gif(
        file: bytes, width: int, height: int, fps: int = 25, **_: Any
    ) -> ConvertedSticker:
        try:
            converted_gif = await _run_lottieconverter(
                args=("-", "-", "gif", f"{width}x{height}", str(fps)),
                input_data=file,
            )
            return ConvertedSticker("image/gif", converted_gif)
        except ffmpeg.ConverterError as e:
            log.error(str(e))
            return ConvertedSticker("application/gzip", file)

    converters["png"] = tgs_to_png
    converters["gif"] = tgs_to_gif

if lottieconverter and ffmpeg.ffmpeg_path:

    async def tgs_to_webm(
        file: bytes, width: int, height: int, fps: int = 30, **_: Any
    ) -> ConvertedSticker:
        with tempfile.TemporaryDirectory(prefix="tgs_") as tmpdir:
            file_template = tmpdir + "/out_"
            try:
                await _run_lottieconverter(
                    args=("-", file_template, "pngs", f"{width}x{height}", str(fps)),
                    input_data=file,
                )
                first_frame_name = min(os.listdir(tmpdir))
                with open(f"{tmpdir}/{first_frame_name}", "rb") as first_frame_file:
                    first_frame_data = first_frame_file.read()
                webm_data = await ffmpeg.convert_path(
                    input_args=("-framerate", str(fps), "-pattern_type", "glob"),
                    input_file=f"{file_template}*.png",
                    output_args=("-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-f", "webm"),
                    output_path_override="-",
                    output_extension=None,
                )
                return ConvertedSticker("video/webm", webm_data, "image/png", first_frame_data)
            except ffmpeg.ConverterError as e:
                log.error(str(e))
        return ConvertedSticker("application/gzip", file)

    async def tgs_to_webp(
        file: bytes, width: int, height: int, fps: int = 30, **_: Any
    ) -> ConvertedSticker:
        with tempfile.TemporaryDirectory(prefix="tgs_") as tmpdir:
            file_template = tmpdir + "/out_"
            try:
                await _run_lottieconverter(
                    args=("-", file_template, "pngs", f"{width}x{height}", str(fps)),
                    input_data=file,
                )
                first_frame_name = min(os.listdir(tmpdir))
                with open(f"{tmpdir}/{first_frame_name}", "rb") as first_frame_file:
                    first_frame_data = first_frame_file.read()
                webp_data = await ffmpeg.convert_path(
                    input_args=("-framerate", str(fps), "-pattern_type", "glob"),
                    input_file=f"{file_template}*.png",
                    output_args=("-c:v", "libwebp_anim", "-pix_fmt", "yuva420p", "-f", "webp"),
                    output_path_override="-",
                    output_extension=None,
                )
                return ConvertedSticker("image/webp", webp_data, "image/png", first_frame_data)
            except ffmpeg.ConverterError as e:
                log.error(str(e))
        return ConvertedSticker("application/gzip", file)

    converters["webm"] = tgs_to_webm
    converters["webp"] = tgs_to_webp


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
