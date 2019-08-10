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
from typing import Optional, Iterator

from sqlalchemy import Column, UniqueConstraint, Integer, String, and_, func, desc, select
from sqlalchemy.engine.result import RowProxy
from sqlalchemy.sql.expression import ClauseElement

from mautrix.types import RoomID, EventID
from mautrix.bridge.db import Base

from ..types import TelegramID


class Message(Base):
    __tablename__ = "message"

    mxid: EventID = Column(String)
    mx_room: RoomID = Column(String)
    tgid: TelegramID = Column(Integer, primary_key=True)
    tg_space: TelegramID = Column(Integer, primary_key=True)
    edit_index: int = Column(Integer, primary_key=True)

    __table_args__ = (UniqueConstraint("mxid", "mx_room", "tg_space", name="_mx_id_room_2"),)

    @classmethod
    def scan(cls, row: RowProxy) -> 'Message':
        return cls(mxid=row[0], mx_room=row[1], tgid=row[2], tg_space=row[3], edit_index=row[4])

    @classmethod
    def get_all_by_tgid(cls, tgid: TelegramID, tg_space: TelegramID) -> Iterator['Message']:
        return cls._all(cls.db.execute(cls.t.select().where(and_(cls.c.tgid == tgid,
                                                                 cls.c.tg_space == tg_space))))

    @classmethod
    def get_one_by_tgid(cls, tgid: TelegramID, tg_space: TelegramID, edit_index: int = 0
                        ) -> Optional['Message']:
        query = cls.t.select()
        if edit_index < 0:
            query = (query
                     .where(and_(cls.c.tgid == tgid, cls.c.tg_space == tg_space))
                     .order_by(desc(cls.c.edit_index))
                     .limit(1)
                     .offset(-edit_index - 1))
        else:
            query = query.where(and_(cls.c.tgid == tgid, cls.c.tg_space == tg_space,
                                     cls.c.edit_index == edit_index))
        return cls._one_or_none(cls.db.execute(query))

    @classmethod
    def count_spaces_by_mxid(cls, mxid: EventID, mx_room: RoomID) -> int:
        rows = cls.db.execute(select([func.count(cls.c.tg_space)])
                              .where(and_(cls.c.mxid == mxid, cls.c.mx_room == mx_room)))
        try:
            count, = next(rows)
            return count
        except StopIteration:
            return 0

    @classmethod
    def get_by_mxid(cls, mxid: EventID, mx_room: RoomID, tg_space: TelegramID
                    ) -> Optional['Message']:
        return cls._select_one_or_none(and_(cls.c.mxid == mxid,
                                            cls.c.mx_room == mx_room,
                                            cls.c.tg_space == tg_space))

    @classmethod
    def update_by_tgid(cls, s_tgid: TelegramID, s_tg_space: TelegramID, s_edit_index: int,
                       **values) -> None:
        with cls.db.begin() as conn:
            conn.execute(cls.t.update()
                         .where(and_(cls.c.tgid == s_tgid, cls.c.tg_space == s_tg_space,
                                     cls.c.edit_index == s_edit_index))
                         .values(**values))

    @classmethod
    def update_by_mxid(cls, s_mxid: EventID, s_mx_room: RoomID, **values) -> None:
        with cls.db.begin() as conn:
            conn.execute(cls.t.update()
                         .where(and_(cls.c.mxid == s_mxid, cls.c.mx_room == s_mx_room))
                         .values(**values))

    @property
    def _edit_identity(self) -> ClauseElement:
        return and_(self.c.tgid == self.tgid, self.c.tg_space == self.tg_space,
                    self.c.edit_index == self.edit_index)

    def insert(self) -> None:
        with self.db.begin() as conn:
            conn.execute(self.t.insert().values(mxid=self.mxid, mx_room=self.mx_room,
                                                tgid=self.tgid, tg_space=self.tg_space,
                                                edit_index=self.edit_index))
