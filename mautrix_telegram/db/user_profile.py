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
from sqlalchemy import Column, String, and_
from typing import Dict, Optional

from ..types import MatrixUserID, MatrixRoomID
from .base import Base


class UserProfile(Base):
    __tablename__ = "mx_user_profile"

    room_id = Column(String, primary_key=True)  # type: MatrixRoomID
    user_id = Column(String, primary_key=True)  # type: MatrixUserID
    membership = Column(String, nullable=False, default="leave")
    displayname = Column(String, nullable=True)
    avatar_url = Column(String, nullable=True)

    def dict(self) -> Dict[str, str]:
        return {
            "membership": self.membership,
            "displayname": self.displayname,
            "avatar_url": self.avatar_url,
        }

    @classmethod
    def get(cls, room_id: MatrixRoomID, user_id: MatrixUserID) -> Optional['UserProfile']:
        rows = cls.db.execute(
            cls.t.select().where(and_(cls.c.room_id == room_id, cls.c.user_id == user_id)))
        try:
            room_id, user_id, membership, displayname, avatar_url = next(rows)
            return cls(room_id=room_id, user_id=user_id, membership=membership,
                       displayname=displayname, avatar_url=avatar_url)
        except StopIteration:
            return None

    @classmethod
    def delete_all(cls, room_id: MatrixRoomID) -> None:
        with cls.db.begin() as conn:
            conn.execute(cls.t.delete().where(cls.c.room_id == room_id))

    def update(self) -> None:
        super().update(membership=self.membership, displayname=self.displayname,
                       avatar_url=self.avatar_url)

    @property
    def _edit_identity(self):
        return and_(self.c.room_id == self.room_id, self.c.user_id == self.user_id)

    def insert(self) -> None:
        with self.db.begin() as conn:
            conn.execute(self.t.insert().values(room_id=self.room_id, user_id=self.user_id,
                                                membership=self.membership,
                                                displayname=self.displayname,
                                                avatar_url=self.avatar_url))
