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
from sqlalchemy import Column, String, Text
from typing import Dict, Optional
import json

from ..types import MatrixRoomID
from .base import Base


class RoomState(Base):
    __tablename__ = "mx_room_state"

    room_id = Column(String, primary_key=True)  # type: MatrixRoomID
    power_levels = Column("power_levels", Text, nullable=True)  # type: Optional[Dict]

    @property
    def _power_levels_text(self) -> Optional[str]:
        return json.dumps(self.power_levels) if self.power_levels else None

    @property
    def has_power_levels(self) -> bool:
        return bool(self.power_levels)

    @classmethod
    def get(cls, room_id: MatrixRoomID) -> Optional['RoomState']:
        rows = cls.db.execute(cls.t.select().where(cls.c.room_id == room_id))
        try:
            room_id, power_levels_text = next(rows)
            return cls(room_id=room_id, power_levels=(json.loads(power_levels_text)
                                                      if power_levels_text else None))
        except StopIteration:
            return None

    def update(self) -> None:
        with self.db.begin() as conn:
            conn.execute(self.t.update()
                         .where(self.c.room_id == self.room_id)
                         .values(power_levels=self._power_levels_text))

    @property
    def _edit_identity(self):
        return self.c.room_id == self.room_id

    def insert(self) -> None:
        with self.db.begin() as conn:
            conn.execute(self.t.insert().values(room_id=self.room_id,
                                                power_levels=self._power_levels_text))
