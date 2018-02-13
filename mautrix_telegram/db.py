# -*- coding: future_fstrings -*-
# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2018 Tulir Asokan
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
from sqlalchemy import Column, UniqueConstraint, ForeignKey, ForeignKeyConstraint, Integer, String
from sqlalchemy.orm import relationship

from .base import Base


class Portal(Base):
    query = None
    __tablename__ = "portal"

    # Telegram chat information
    tgid = Column(Integer, primary_key=True)
    tg_receiver = Column(Integer, primary_key=True)
    peer_type = Column(String)

    # Matrix portal information
    mxid = Column(String, unique=True, nullable=True)

    # Telegram chat metadata
    username = Column(String, nullable=True)
    title = Column(String, nullable=True)
    about = Column(String, nullable=True)
    photo_id = Column(String, nullable=True)


class Message(Base):
    query = None
    __tablename__ = "message"

    mxid = Column(String)
    mx_room = Column(String)
    tgid = Column(Integer, primary_key=True)
    tg_space = Column(Integer, primary_key=True)

    __table_args__ = (UniqueConstraint('mxid', 'mx_room', 'tg_space', name='_mx_id_room'),)


class UserPortal(Base):
    query = None
    __tablename__ = "user_portal"

    user = Column(Integer, ForeignKey("user.tgid"), primary_key=True)
    portal = Column(Integer, primary_key=True)
    portal_receiver = Column(Integer, primary_key=True)

    __table_args__ = (ForeignKeyConstraint(("portal", "portal_receiver"),
                                           ("portal.tgid", "portal.tg_receiver")),)


class User(Base):
    query = None
    __tablename__ = "user"

    mxid = Column(String, primary_key=True)
    tgid = Column(Integer, nullable=True)
    tg_username = Column(String, nullable=True)
    saved_contacts = Column(Integer, default=0)
    contacts = relationship("Contact", uselist=True,
                            cascade="save-update, merge, delete, delete-orphan")
    portals = relationship("Portal", secondary="user_portal", single_parent=True,
                           cascade="save-update, merge, delete, delete-orphan")


class Contact(Base):
    query = None
    __tablename__ = "contact"

    user = Column("user", Integer, ForeignKey("user.tgid"), primary_key=True)
    contact = Column("contact", Integer, ForeignKey("puppet.id"), primary_key=True)


class Puppet(Base):
    query = None
    __tablename__ = "puppet"

    id = Column(Integer, primary_key=True)
    displayname = Column(String, nullable=True)
    username = Column(String, nullable=True)
    photo_id = Column(String, nullable=True)


def init(db_session):
    Portal.query = db_session.query_property()
    Message.query = db_session.query_property()
    UserPortal.query = db_session.query_property()
    User.query = db_session.query_property()
    Puppet.query = db_session.query_property()
