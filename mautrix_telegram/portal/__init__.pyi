from typing import Union
from .base import BasePortal
from .portal_matrix import PortalMatrix
from .portal_metadata import PortalMetadata
from .portal_telegram import PortalTelegram
from ..context import Context

Portal = Union[BasePortal, PortalMatrix, PortalMetadata, PortalTelegram]


def init(context: Context) -> None:
	pass


__all__ = ["Portal", "init"]
