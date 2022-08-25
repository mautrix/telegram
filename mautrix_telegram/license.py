# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2021 Tulir Asokan
# Copyright (C) 2022 New Vector Ltd
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

from uuid import uuid4
import logging
import os

_instance_id: str | None = None


def get_instance_id(default_id: str, log: logging.Logger = logging.getLogger()) -> str:
    global _instance_id
    if not _instance_id:
        licence_file_path = os.environ.get("MAUTRIX_TELEGRAM_LICENCE_PATH")
        if not licence_file_path and default_id:
            _instance_id = default_id
        else:
            if not licence_file_path:
                licence_file_path = os.path.abspath("../instanceId")
            try:
                with open(licence_file_path) as licence_file:
                    _instance_id = licence_file.read().strip()
            except:
                log.info("Licence ID not present. Generating new key...")
                _instance_id = generate_instance_id()
                try:
                    with open(licence_file_path, "w") as licence_file:
                        licence_file.write(_instance_id)
                except Exception as e:
                    log.error(f"Failed to write licence key {_instance_id} to disk ({e})")

    return _instance_id


def generate_instance_id() -> str:
    return str(uuid4())
