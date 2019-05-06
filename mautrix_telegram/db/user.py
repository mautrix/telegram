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
from sqlalchemy import Column, ForeignKey, ForeignKeyConstraint, Integer, String
from sqlalchemy.engine.result import RowProxy
from typing import Optional, Iterable, Tuple

from ..types import MatrixUserID, TelegramID
from .base import Base


class User(Base):
    __tablename__ = "user"

    mxid = Column(String, primary_key=True)  # type: MatrixUserID
    tgid = Column(Integer, nullable=True, unique=True)  # type: Optional[TelegramID]
    tg_username = Column(String, nullable=True)
    tg_phone = Column(String, nullable=True)
    saved_contacts = Column(Integer, default=0, nullable=False)

    @classmethod
    def _one_or_none(cls, rows: RowProxy) -> Optional['User']:
        try:
            mxid, tgid, tg_username, tg_phone, saved_contacts = next(rows)
            return cls(mxid=mxid, tgid=tgid, tg_username=tg_username, tg_phone=tg_phone,
                       saved_contacts=saved_contacts)
        except StopIteration:
            return None

    @classmethod
    def all(cls) -> Iterable['User']:
        rows = cls.db.execute(cls.t.select())
        for row in rows:
            mxid, tgid, tg_username, tg_phone, saved_contacts = row
            yield cls(mxid=mxid, tgid=tgid, tg_username=tg_username, tg_phone=tg_phone,
                      saved_contacts=saved_contacts)

    @classmethod
    def get_by_tgid(cls, tgid: TelegramID) -> Optional['User']:
        return cls._select_one_or_none(cls.c.tgid == tgid)

    @classmethod
    def get_by_mxid(cls, mxid: MatrixUserID) -> Optional['User']:
        return cls._select_one_or_none(cls.c.mxid == mxid)

    @classmethod
    def get_by_username(cls, username: str) -> Optional['User']:
        return cls._select_one_or_none(cls.c.tg_username == username)

    @property
    def _edit_identity(self):
        return self.c.mxid == self.mxid

    def insert(self) -> None:
        with self.db.begin() as conn:
            conn.execute(self.t.insert().values(
                mxid=self.mxid, tgid=self.tgid, tg_username=self.tg_username,
                tg_phone=self.tg_phone, saved_contacts=self.saved_contacts))

    @property
    def contacts(self) -> Iterable[TelegramID]:
        rows = self.db.execute(Contact.t.select().where(Contact.c.user == self.tgid))
        for row in rows:
            user, contact = row
            yield contact

    @contacts.setter
    def contacts(self, puppets: Iterable[TelegramID]) -> None:
        with self.db.begin() as conn:
            conn.execute(Contact.t.delete().where(Contact.c.user == self.tgid))
            insert_puppets = [{"user": self.tgid, "contact": tgid} for tgid in puppets]
            if insert_puppets:
                conn.execute(Contact.t.insert(), insert_puppets)

    @property
    def portals(self) -> Iterable[Tuple[TelegramID, TelegramID]]:
        rows = self.db.execute(UserPortal.t.select().where(UserPortal.c.user == self.tgid))
        for row in rows:
            user, portal, portal_receiver = row
            yield (portal, portal_receiver)

    @portals.setter
    def portals(self, portals: Iterable[Tuple[TelegramID, TelegramID]]) -> None:
        with self.db.begin() as conn:
            conn.execute(UserPortal.t.delete().where(UserPortal.c.user == self.tgid))
            insert_portals = [{
                "user": self.tgid,
                "portal": tgid,
                "portal_receiver": tg_receiver
            } for tgid, tg_receiver in portals]
            if insert_portals:
                conn.execute(UserPortal.t.insert(), insert_portals)

    def delete(self) -> None:
        super().delete()
        self.portals = []
        self.contacts = []


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
