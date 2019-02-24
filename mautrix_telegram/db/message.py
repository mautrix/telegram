# -*- coding: future_fstrings -*-
# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2018 Tulir Asokan
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
from sqlalchemy import Column, UniqueConstraint, Integer, String, and_, func, select
from sqlalchemy.engine.result import RowProxy
from typing import Optional, List

from ..types import MatrixRoomID, MatrixEventID, TelegramID
from .base import Base


class Message(Base):
    __tablename__ = "message"

    mxid = Column(String)  # type: MatrixEventID
    mx_room = Column(String)  # type: MatrixRoomID
    tgid = Column(Integer, primary_key=True)  # type: TelegramID
    tg_space = Column(Integer, primary_key=True)  # type: TelegramID

    __table_args__ = (UniqueConstraint("mxid", "mx_room", "tg_space", name="_mx_id_room"),)

    @classmethod
    def _one_or_none(cls, rows: RowProxy) -> Optional['Message']:
        try:
            mxid, mx_room, tgid, tg_space = next(rows)
            return cls(mxid=mxid, mx_room=mx_room, tgid=tgid, tg_space=tg_space)
        except StopIteration:
            return None

    @staticmethod
    def _all(rows: RowProxy) -> List['Message']:
        return [Message(mxid=row[0], mx_room=row[1], tgid=row[2], tg_space=row[3])
                for row in rows]

    @classmethod
    def get_by_tgid(cls, tgid: TelegramID, tg_space: TelegramID) -> Optional['Message']:
        return cls._select_one_or_none(and_(cls.c.tgid == tgid, cls.c.tg_space == tg_space))

    @classmethod
    def count_spaces_by_mxid(cls, mxid: MatrixEventID, mx_room: MatrixRoomID) -> int:
        rows = cls.db.execute(select([func.count(cls.c.tg_space)])
                              .where(and_(cls.c.mxid == mxid, cls.c.mx_room == mx_room)))
        try:
            count, = next(rows)
            return count
        except StopIteration:
            return 0

    @classmethod
    def get_by_mxid(cls, mxid: MatrixEventID, mx_room: MatrixRoomID, tg_space: TelegramID
                    ) -> Optional['Message']:
        return cls._select_one_or_none(and_(cls.c.mxid == mxid,
                                            cls.c.mx_room == mx_room,
                                            cls.c.tg_space == tg_space))

    @classmethod
    def update_by_tgid(cls, s_tgid: TelegramID, s_tg_space: TelegramID, **values) -> None:
        with cls.db.begin() as conn:
            conn.execute(cls.t.update()
                         .where(and_(cls.c.tgid == s_tgid, cls.c.tg_space == s_tg_space))
                         .values(**values))

    @classmethod
    def update_by_mxid(cls, s_mxid: MatrixEventID, s_mx_room: MatrixRoomID, **values) -> None:
        with cls.db.begin() as conn:
            conn.execute(cls.t.update()
                         .where(and_(cls.c.mxid == s_mxid, cls.c.mx_room == s_mx_room))
                         .values(**values))

    @property
    def _edit_identity(self):
        return and_(self.c.tgid == self.tgid, self.c.tg_space == self.tg_space)

    def insert(self) -> None:
        with self.db.begin() as conn:
            conn.execute(self.t.insert().values(mxid=self.mxid, mx_room=self.mx_room,
                                                tgid=self.tgid, tg_space=self.tg_space))
