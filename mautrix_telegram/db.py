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
from sqlalchemy import (Column, UniqueConstraint, ForeignKey, ForeignKeyConstraint, Integer,
                        BigInteger, String, Boolean, Text, Table,
                        and_, func, select)
from sqlalchemy.engine import Engine, RowProxy
from sqlalchemy.sql import expression
from sqlalchemy.orm import relationship, Query
from sqlalchemy.sql.base import ImmutableColumnCollection
from typing import Dict, Optional, List
import json

from mautrix_telegram.types import MatrixUserID, MatrixRoomID, MatrixEventID
from .types import TelegramID
from .base import Base


class Portal(Base):
    query = None  # type: Query
    __tablename__ = "portal"

    # Telegram chat information
    tgid = Column(Integer, primary_key=True)  # type: TelegramID
    tg_receiver = Column(Integer, primary_key=True)  # type: TelegramID
    peer_type = Column(String, nullable=False)
    megagroup = Column(Boolean)

    # Matrix portal information
    mxid = Column(String, unique=True, nullable=True)  # type: Optional[MatrixRoomID]

    config = Column(Text, nullable=True)

    # Telegram chat metadata
    username = Column(String, nullable=True)
    title = Column(String, nullable=True)
    about = Column(String, nullable=True)
    photo_id = Column(String, nullable=True)


class Message(Base):
    db = None  # type: Engine
    t = None  # type: Table
    c = None  # type: ImmutableColumnCollection
    __tablename__ = "message"

    mxid = Column(String)  # type: MatrixEventID
    mx_room = Column(String)  # type: MatrixRoomID
    tgid = Column(Integer, primary_key=True)  # type: TelegramID
    tg_space = Column(Integer, primary_key=True)  # type: TelegramID

    __table_args__ = (UniqueConstraint("mxid", "mx_room", "tg_space", name="_mx_id_room"),)

    @staticmethod
    def _one_or_none(rows: RowProxy) -> Optional['Message']:
        try:
            mxid, mx_room, tgid, tg_space = next(rows)
            return Message(mxid=mxid, mx_room=mx_room, tgid=tgid, tg_space=tg_space)
        except StopIteration:
            return None

    @staticmethod
    def _all(rows: RowProxy) -> List['Message']:
        return [Message(mxid=row[0], mx_room=row[1], tgid=row[2], tg_space=row[3])
                for row in rows]

    @classmethod
    def get_by_tgid(cls, tgid: TelegramID, tg_space: TelegramID) -> Optional['Message']:
        rows = cls.db.execute(cls.t.select()
                              .where(and_(cls.c.tgid == tgid, cls.c.tg_space == tg_space)))
        return cls._one_or_none(rows)

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
        rows = cls.db.execute(cls.t.select().where(
            and_(cls.c.mxid == mxid, cls.c.mx_room == mx_room, cls.c.tg_space == tg_space)))
        return cls._one_or_none(rows)

    @classmethod
    def update_by_tgid(cls, s_tgid: TelegramID, s_tg_space: TelegramID, **values) -> None:
        cls.db.execute(cls.t.update()
                       .where(and_(cls.c.tgid == s_tgid, cls.c.tg_space == s_tg_space))
                       .values(**values))

    @classmethod
    def update_by_mxid(cls, s_mxid: MatrixEventID, s_mx_room: MatrixRoomID, **values) -> None:
        cls.db.execute(cls.t.update()
                       .where(and_(cls.c.mxid == s_mxid, cls.c.mx_room == s_mx_room))
                       .values(**values))

    def update(self, **values) -> None:
        for key, value in values.items():
            setattr(self, key, value)
        self.update_by_tgid(self.tgid, self.tg_space, **values)

    def delete(self) -> None:
        self.db.execute(self.t.delete().where(
            and_(self.c.tgid == self.tgid, self.c.tg_space == self.tg_space)))

    def insert(self) -> None:
        self.db.execute(self.t.insert().values(mxid=self.mxid, mx_room=self.mx_room, tgid=self.tgid,
                                               tg_space=self.tg_space))


class UserPortal(Base):
    query = None  # type: Query
    __tablename__ = "user_portal"

    user = Column(Integer, ForeignKey("user.tgid", onupdate="CASCADE", ondelete="CASCADE"),
                  primary_key=True)  # type: TelegramID
    portal = Column(Integer, primary_key=True)  # type: TelegramID
    portal_receiver = Column(Integer, primary_key=True)  # type: TelegramID

    __table_args__ = (ForeignKeyConstraint(("portal", "portal_receiver"),
                                           ("portal.tgid", "portal.tg_receiver"),
                                           onupdate="CASCADE", ondelete="CASCADE"),)


class User(Base):
    query = None  # type: Query
    __tablename__ = "user"

    mxid = Column(String, primary_key=True)  # type: MatrixUserID
    tgid = Column(Integer, nullable=True, unique=True)  # type: Optional[TelegramID]
    tg_username = Column(String, nullable=True)
    tg_phone = Column(String, nullable=True)
    saved_contacts = Column(Integer, default=0, nullable=False)
    contacts = relationship("Contact", uselist=True,
                            cascade="save-update, merge, delete, delete-orphan"
                            )  # type: List[Contact]
    portals = relationship("Portal", secondary="user_portal")


class RoomState(Base):
    query = None  # type: Query
    __tablename__ = "mx_room_state"

    room_id = Column(String, primary_key=True)  # type: MatrixRoomID
    _power_levels_text = Column("power_levels", Text, nullable=True)
    _power_levels_json = {}  # type: Dict

    @property
    def has_power_levels(self) -> bool:
        return bool(self._power_levels_text)

    @property
    def power_levels(self) -> Dict:
        if not self._power_levels_json and self._power_levels_text:
            self._power_levels_json = json.loads(self._power_levels_text)
        return self._power_levels_json

    @power_levels.setter
    def power_levels(self, val: Dict) -> None:
        self._power_levels_json = val
        self._power_levels_text = json.dumps(val)


class UserProfile(Base):
    query = None  # type: Query
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


class Contact(Base):
    query = None  # type: Query
    __tablename__ = "contact"

    user = Column(Integer, ForeignKey("user.tgid"), primary_key=True)  # type: TelegramID
    contact = Column(Integer, ForeignKey("puppet.id"), primary_key=True)  # type: TelegramID


class Puppet(Base):
    query = None  # type: Query
    __tablename__ = "puppet"

    id = Column(Integer, primary_key=True)  # type: TelegramID
    custom_mxid = Column(String, nullable=True)  # type: Optional[MatrixUserID]
    access_token = Column(String, nullable=True)
    displayname = Column(String, nullable=True)
    displayname_source = Column(Integer, nullable=True)  # type: Optional[TelegramID]
    username = Column(String, nullable=True)
    photo_id = Column(String, nullable=True)
    is_bot = Column(Boolean, nullable=True)
    matrix_registered = Column(Boolean, nullable=False, server_default=expression.false())


# Fucking Telegram not telling bots what chats they are in 3:<
class BotChat(Base):
    query = None  # type: Query
    __tablename__ = "bot_chat"
    id = Column(Integer, primary_key=True)  # type: TelegramID
    type = Column(String, nullable=False)


class TelegramFile(Base):
    query = None  # type: Query
    __tablename__ = "telegram_file"

    id = Column(String, primary_key=True)
    mxc = Column(String)
    mime_type = Column(String)
    was_converted = Column(Boolean)
    timestamp = Column(BigInteger)
    size = Column(Integer, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    thumbnail_id = Column("thumbnail", String, ForeignKey("telegram_file.id"), nullable=True)
    thumbnail = relationship("TelegramFile", uselist=False)


def init(db_session, db_engine) -> None:
    Portal.query = db_session.query_property()
    Message.db = db_engine
    Message.t = Message.__table__
    Message.c = Message.t.c
    UserPortal.query = db_session.query_property()
    User.query = db_session.query_property()
    Puppet.query = db_session.query_property()
    BotChat.query = db_session.query_property()
    TelegramFile.query = db_session.query_property()
    UserProfile.query = db_session.query_property()
    RoomState.query = db_session.query_property()
