from abc import abstractmethod

from sqlalchemy import Table
from sqlalchemy.engine.base import Engine
from sqlalchemy.engine.result import RowProxy
from sqlalchemy.sql.base import ImmutableColumnCollection
from sqlalchemy.ext.declarative import declarative_base

class Base(declarative_base):
	db: Engine
	t: Table
	__table__: Table
	c: ImmutableColumnCollection

    @classmethod
    @abstractmethod
    def _one_or_none(cls, rows: RowProxy): ...

	@classmethod
	def _select_one_or_none(cls, *args): ...

    def _edit_identity(self): ...

    def update(self, **values) -> None: ...

    def delete(self) -> None: ...
