# -*- coding: future_fstrings -*-
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
import mimetypes

mimetypes.init()

sanity_overrides = {
    "image/jpeg": ".jpeg",
    "image/tiff": ".tiff",
    "text/plain": ".txt",
    "text/html": ".html",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "application/xml": ".xml",
    "application/octet-stream": "",
    "application/x-msdos-program": ".exe",
}


def guess_extension(mime: str) -> str:
    try:
        return sanity_overrides[mime]
    except KeyError:
        return mimetypes.guess_extension(mime)
