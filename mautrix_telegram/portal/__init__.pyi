from typing import Union
from .base import BasePortal
from .matrix import PortalMatrix
from .metadata import PortalMetadata
from .telegram import PortalTelegram
from ..context import Context

Portal = Union[BasePortal, PortalMatrix, PortalMetadata, PortalTelegram]


def init(context: Context) -> None:
	pass


__all__ = ["Portal", "init"]
