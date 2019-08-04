from .base import BasePortal, init as init_base
from .portal_matrix import PortalMatrix
from .portal_metadata import PortalMetadata
from .portal_telegram import PortalTelegram
from .deduplication import init as init_dedup
from ..context import Context


class Portal(BasePortal, PortalMatrix, PortalTelegram, PortalMetadata):
    pass


def init(context: Context) -> None:
    init_base(context)
    init_dedup(context)


__all__ = ["Portal", "BasePortal", "init"]
