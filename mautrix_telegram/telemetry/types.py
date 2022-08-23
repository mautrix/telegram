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

from attr import dataclass

from mautrix.types import SerializableAttrs

TELEMETRY_TYPE = "net.maunium.telegram"


@dataclass
class TelemetryVersion(SerializableAttrs):
    """
    Telemetry payload properties that describe the format of payloads sent by
    the current version of the bridge.
    """
    version = 1
    type = TELEMETRY_TYPE


@dataclass
class TelemetryInstance(SerializableAttrs):
    """Telemetry payload properties that depend on bridge configuration."""
    instanceId: str
    hostname: str


@dataclass
class TelemetryEvent(TelemetryVersion, TelemetryInstance):
    """Top-level class for telemetry event payloads."""
    generationTime: int
    data: TelemetryData


@dataclass
class TelemetryData(SerializableAttrs):
    rmau: TelemetryDataRMAU


@dataclass
class TelemetryDataRMAU(SerializableAttrs):
    allUsers: int
