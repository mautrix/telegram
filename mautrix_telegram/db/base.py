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
from abc import abstractmethod

from sqlalchemy import Table
from sqlalchemy.engine.base import Engine
from sqlalchemy.engine.result import RowProxy
from sqlalchemy.sql.base import ImmutableColumnCollection
from sqlalchemy.ext.declarative import declarative_base


class BaseBase:
    db = None  # type: Engine
    t = None  # type: Table
    __table__ = None  # type: Table
    c = None  # type: ImmutableColumnCollection

    @classmethod
    @abstractmethod
    def _one_or_none(cls, rows: RowProxy):
        pass

    @classmethod
    def _select_one_or_none(cls, *args):
        return cls._one_or_none(cls.db.execute(cls.t.select().where(*args)))

    @property
    @abstractmethod
    def _edit_identity(self):
        pass

    def update(self, **values) -> None:
        with self.db.begin() as conn:
            conn.execute(self.t.update()
                         .where(self._edit_identity)
                         .values(**values))
        for key, value in values.items():
            setattr(self, key, value)

    def delete(self) -> None:
        with self.db.begin() as conn:
            conn.execute(self.t.delete().where(self._edit_identity))

Base = declarative_base(cls=BaseBase)
