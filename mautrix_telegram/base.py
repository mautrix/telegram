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
        self.db.execute(self.t.update()
                        .where(self._edit_identity)
                        .values(**values))
        for key, value in values.items():
            setattr(self, key, value)

    def delete(self) -> None:
        self.db.execute(self.t.delete().where(self._edit_identity))


Base = declarative_base(cls=BaseBase)
