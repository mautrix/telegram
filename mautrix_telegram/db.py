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
                        BigInteger, String, Boolean, Text,
                        and_, func, select)
from sqlalchemy.engine.result import RowProxy
from sqlalchemy.sql import expression
from sqlalchemy.orm import relationship, Query
from typing import Dict, Optional, List, Iterable
import json

from mautrix_telegram.types import MatrixUserID, MatrixRoomID, MatrixEventID
from .types import TelegramID
from .base import Base


class Portal(Base):
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

    @classmethod
    def _one_or_none(cls, rows: RowProxy) -> Optional['Portal']:
        try:
            (tgid, tg_receiver, peer_type, megagroup, mxid, config,
             username, title, about, photo_id) = next(rows)
            return cls(tgid=tgid, tg_receiver=tg_receiver, peer_type=peer_type,
                       megagroup=megagroup, mxid=mxid, config=config, username=username,
                       title=title, about=about, photo_id=photo_id)
        except StopIteration:
            return None

    @classmethod
    def get_by_tgid(cls, tgid: TelegramID, tg_receiver: TelegramID) -> Optional['Portal']:
        return cls._select_one_or_none(and_(cls.c.tgid == tgid, cls.c.tg_receiver == tg_receiver))

    @classmethod
    def get_by_mxid(cls, mxid: MatrixRoomID) -> Optional['Portal']:
        return cls._select_one_or_none(cls.c.mxid == mxid)

    @classmethod
    def get_by_username(cls, username: str) -> Optional['Portal']:
        return cls._select_one_or_none(cls.c.username == username)

    @property
    def _edit_identity(self):
        return and_(self.c.tgid == self.tgid, self.c.tg_receiver == self.tg_receiver)

    def insert(self) -> None:
        self.db.execute(self.t.insert().values(
            tgid=self.tgid, tg_receiver=self.tg_receiver, peer_type=self.peer_type,
            megagroup=self.megagroup, mxid=self.mxid, config=self.config, username=self.username,
            title=self.title, about=self.about, photo_id=self.photo_id))


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
        cls.db.execute(cls.t.update()
                       .where(and_(cls.c.tgid == s_tgid, cls.c.tg_space == s_tg_space))
                       .values(**values))

    @classmethod
    def update_by_mxid(cls, s_mxid: MatrixEventID, s_mx_room: MatrixRoomID, **values) -> None:
        cls.db.execute(cls.t.update()
                       .where(and_(cls.c.mxid == s_mxid, cls.c.mx_room == s_mx_room))
                       .values(**values))

    @property
    def _edit_identity(self):
        return and_(self.c.tgid == self.tgid, self.c.tg_space == self.tg_space)

    def insert(self) -> None:
        self.db.execute(self.t.insert().values(mxid=self.mxid, mx_room=self.mx_room, tgid=self.tgid,
                                               tg_space=self.tg_space))


class User(Base):
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

    @classmethod
    def _one_or_none(cls, rows: RowProxy) -> Optional['User']:
        try:
            mxid, tgid, tg_username, tg_phone, saved_contacts = next(rows)
            return cls(mxid=mxid, tgid=tgid, tg_username=tg_username, tg_phone=tg_phone,
                       saved_contacts=saved_contacts)
        except StopIteration:
            return None

    @classmethod
    def get_all(cls) -> Iterable['User']:
        rows = cls.db.execute(cls.t.select())
        for row in rows:
            mxid, tgid, tg_username, tg_phone, saved_contacts = row
            yield cls(mxid=mxid, tgid=tgid, tg_username=tg_username, tg_phone=tg_phone,
                      saved_contacts=saved_contacts)

    @classmethod
    def get_by_tgid(cls, tgid: TelegramID) -> Optional['User']:
        return cls._select_one_or_none(cls.c.tgid == tgid)

    @classmethod
    def get_by_mxid(cls, mxid: MatrixRoomID) -> Optional['User']:
        return cls._select_one_or_none(cls.c.mxid == mxid)

    @classmethod
    def get_by_username(cls, username: str) -> Optional['User']:
        return cls._select_one_or_none(cls.c.username == username)

    @property
    def _edit_identity(self):
        return self.c.mxid == self.mxid

    def insert(self) -> None:
        self.db.execute(self.t.insert().values(
            mxid=self.mxid, tgid=self.tgid, tg_username=self.tg_username, tg_phone=self.tg_phone,
            saved_contacts=self.saved_contacts))


class UserPortal(Base):
    __tablename__ = "user_portal"

    user = Column(Integer, ForeignKey("user.tgid", onupdate="CASCADE", ondelete="CASCADE"),
                  primary_key=True)  # type: TelegramID
    portal = Column(Integer, primary_key=True)  # type: TelegramID
    portal_receiver = Column(Integer, primary_key=True)  # type: TelegramID

    __table_args__ = (ForeignKeyConstraint(("portal", "portal_receiver"),
                                           ("portal.tgid", "portal.tg_receiver"),
                                           onupdate="CASCADE", ondelete="CASCADE"),)


class Contact(Base):
    __tablename__ = "contact"

    user = Column(Integer, ForeignKey("user.tgid"), primary_key=True)  # type: TelegramID
    contact = Column(Integer, ForeignKey("puppet.id"), primary_key=True)  # type: TelegramID


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
        return super().update(power_levels=self._power_levels_text)

    @property
    def _edit_identity(self):
        return self.c.room_id == self.room_id

    def insert(self) -> None:
        self.db.execute(self.t.insert().values(room_id=self.room_id,
                                               power_levels=self._power_levels_text))


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
        cls.db.execute(cls.t.delete().where(cls.c.room_id == room_id))

    def update(self) -> None:
        super().update(membership=self.membership, displayname=self.displayname,
                       avatar_url=self.avatar_url)

    @property
    def _edit_identity(self):
        return and_(self.c.room_id == self.room_id, self.c.user_id == self.user_id)

    def insert(self) -> None:
        self.db.execute(self.t.insert().values(room_id=self.room_id, user_id=self.user_id,
                                               membership=self.membership,
                                               displayname=self.displayname,
                                               avatar_url=self.avatar_url))


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

    @classmethod
    def get(cls, id: str) -> Optional['TelegramFile']:
        rows = cls.db.execute(cls.t.select().where(cls.c.id == id))
        try:
            id, mxc, mime, conv, ts, s, w, h, thumb_id = next(rows)
            thumb = None
            if thumb_id:
                thumb = cls.get(thumb_id)
            return cls(id=id, mxc=mxc, mime_type=mime, was_converted=conv, timestamp=ts,
                       size=s, width=w, height=h, thumbnail_id=thumb_id, thumbnail=thumb)
        except StopIteration:
            return None

    def insert(self) -> None:
        self.db.execute(self.t.insert().values(
            id=self.id, mxc=self.mxc, mime_type=self.mime_type, was_converted=self.was_converted,
            timestamp=self.timestamp, size=self.size, width=self.width, height=self.height,
            thumbnail=self.thumbnail.id if self.thumbnail else self.thumbnail_id))


def init(db_session, db_engine) -> None:
    query = db_session.query_property()
    for table in (Portal, Message, User, Puppet, BotChat, TelegramFile, UserProfile, RoomState):
        table.query = query
        table.db = db_engine
        table.t = table.__table__
        table.c = table.t.c
