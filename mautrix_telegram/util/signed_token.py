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
from typing import Dict, Optional
import json
import base64
import hashlib


def _get_checksum(key: str, payload: bytes) -> str:
    hasher = hashlib.sha256()
    hasher.update(payload)
    hasher.update(key.encode("utf-8"))
    checksum = hasher.hexdigest()
    return checksum


def sign_token(key: str, payload: Dict) -> str:
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8"))
    checksum = _get_checksum(key, payload_b64)
    return f"{checksum}:{payload_b64.decode('utf-8')}"


def verify_token(key: str, data: str) -> Optional[Dict]:
    if not data:
        return None

    try:
        checksum, payload = data.split(":", 1)
    except ValueError:
        return None

    if checksum != _get_checksum(key, payload.encode("utf-8")):
        return None

    payload = base64.urlsafe_b64decode(payload).decode("utf-8")
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None
