from .base import BasePortal, init as init_base
from .portal_matrix import PortalMatrix, init as init_matrix
from .portal_metadata import PortalMetadata, init as init_metadata
from .portal_telegram import PortalTelegram, init as init_telegram
from .deduplication import init as init_dedup
from ..context import Context


class Portal(BasePortal, PortalMatrix, PortalTelegram, PortalMetadata):
    pass


def init(context: Context) -> None:
    init_base(context)
    init_dedup(context)
    init_metadata(context)
    init_telegram(context)
    init_matrix(context)


__all__ = ["Portal", "init"]
