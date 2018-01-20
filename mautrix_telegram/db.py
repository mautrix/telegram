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
from sqlalchemy import orm, \
    Column, ForeignKey, \
    Integer, String
from sqlalchemy.orm.scoping import scoped_session
from .base import Base


class Portal(Base):
    __tablename__ = "portal"

    tgid = Column(Integer, primary_key=True)
    peer_type = Column(String)
    mxid = Column(String, unique=True, nullable=True)


class User(Base):
    __tablename__ = "user"

    mxid = Column(String, primary_key=True)
    tgid = Column(Integer, nullable=True)

    def __init__(self, mxid, tgid=None):
        self.mxid = mxid
        self.tgid = tgid


class Puppet(Base):
    __tablename__ = "puppet"

    id = Column(Integer, primary_key=True)
    displayname = Column(String, nullable=True)


def init(db_factory):
    db = scoped_session(db_factory)
    Portal.query = db.query_property()
    User.query = db.query_property()
    Puppet.query = db.query_property()
