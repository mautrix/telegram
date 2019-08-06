from .base import BasePortal, init as init_base
from .matrix import PortalMatrix, init as init_matrix
from .metadata import PortalMetadata, init as init_metadata
from .telegram import PortalTelegram, init as init_telegram
from .deduplication import init as init_dedup
from ..context import Context


class Portal(PortalMatrix, PortalTelegram, PortalMetadata):
    pass


def init(context: Context) -> None:
    init_base(context)
    init_dedup(context)
    init_metadata(context)
    init_telegram(context)
    init_matrix(context)


__all__ = ["Portal", "init"]
