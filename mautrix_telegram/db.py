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
                        BigInteger, String, Boolean, Text)
from sqlalchemy.sql import expression
from sqlalchemy.orm import relationship, Query
import json

from .base import Base


class Portal(Base):
    query = None  # type: Query
    __tablename__ = "portal"

    # Telegram chat information
    tgid = Column(Integer, primary_key=True)
    tg_receiver = Column(Integer, primary_key=True)
    peer_type = Column(String, nullable=False)
    megagroup = Column(Boolean)

    # Matrix portal information
    mxid = Column(String, unique=True, nullable=True)

    # Telegram chat metadata
    username = Column(String, nullable=True)
    title = Column(String, nullable=True)
    about = Column(String, nullable=True)
    photo_id = Column(String, nullable=True)


class Message(Base):
    query = None  # type: Query
    __tablename__ = "message"

    mxid = Column(String)
    mx_room = Column(String)
    tgid = Column(Integer, primary_key=True)
    tg_space = Column(Integer, primary_key=True)

    __table_args__ = (UniqueConstraint("mxid", "mx_room", "tg_space", name="_mx_id_room"),)


class UserPortal(Base):
    query = None  # type: Query
    __tablename__ = "user_portal"

    user = Column(Integer, ForeignKey("user.tgid", onupdate="CASCADE", ondelete="CASCADE"),
                  primary_key=True)
    portal = Column(Integer, primary_key=True)
    portal_receiver = Column(Integer, primary_key=True)

    __table_args__ = (ForeignKeyConstraint(("portal", "portal_receiver"),
                                           ("portal.tgid", "portal.tg_receiver"),
                                           onupdate="CASCADE", ondelete="CASCADE"),)


class User(Base):
    query = None  # type: Query
    __tablename__ = "user"

    mxid = Column(String, primary_key=True)
    tgid = Column(Integer, nullable=True, unique=True)
    tg_username = Column(String, nullable=True)
    saved_contacts = Column(Integer, default=0, nullable=False)
    contacts = relationship("Contact", uselist=True,
                            cascade="save-update, merge, delete, delete-orphan")
    portals = relationship("Portal", secondary="user_portal")


class RoomState(Base):
    query = None  # type: Query
    __tablename__ = "mx_room_state"

    room_id = Column(String, primary_key=True)
    _power_levels_text = Column("power_levels", Text, nullable=True)
    _power_levels_json = None

    @property
    def has_power_levels(self):
        return bool(self._power_levels_text)

    @property
    def power_levels(self):
        if not self._power_levels_json and self._power_levels_text:
            self._power_levels_json = json.loads(self._power_levels_text)
        return self._power_levels_json or {}

    @power_levels.setter
    def power_levels(self, val):
        self._power_levels_json = val
        self._power_levels_text = json.dumps(val)


class UserProfile(Base):
    query = None  # type: Query
    __tablename__ = "mx_user_profile"

    room_id = Column(String, primary_key=True)
    user_id = Column(String, primary_key=True)
    membership = Column(String, nullable=False, default="leave")
    displayname = Column(String, nullable=True)
    avatar_url = Column(String, nullable=True)

    def dict(self):
        return {
            "membership": self.membership,
            "displayname": self.displayname,
            "avatar_url": self.avatar_url,
        }


class Contact(Base):
    query = None  # type: Query
    __tablename__ = "contact"

    user = Column(Integer, ForeignKey("user.tgid"), primary_key=True)
    contact = Column(Integer, ForeignKey("puppet.id"), primary_key=True)


class Puppet(Base):
    query = None  # type: Query
    __tablename__ = "puppet"

    id = Column(Integer, primary_key=True)
    custom_mxid = Column(String, nullable=True)
    access_token = Column(String, nullable=True)
    displayname = Column(String, nullable=True)
    displayname_source = Column(Integer, nullable=True)
    username = Column(String, nullable=True)
    photo_id = Column(String, nullable=True)
    is_bot = Column(Boolean, nullable=True)
    matrix_registered = Column(Boolean, nullable=False, server_default=expression.false())


# Fucking Telegram not telling bots what chats they are in 3:<
class BotChat(Base):
    query = None  # type: Query
    __tablename__ = "bot_chat"
    id = Column(Integer, primary_key=True)
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


def init(db_session):
    Portal.query = db_session.query_property()
    Message.query = db_session.query_property()
    UserPortal.query = db_session.query_property()
    User.query = db_session.query_property()
    Puppet.query = db_session.query_property()
    BotChat.query = db_session.query_property()
    TelegramFile.query = db_session.query_property()
    UserProfile.query = db_session.query_property()
    RoomState.query = db_session.query_property()
