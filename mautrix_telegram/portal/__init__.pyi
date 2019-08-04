from typing import Union
from .base import BasePortal, init as init_base
from .portal_matrix import PortalMatrix
from .portal_metadata import PortalMetadata
from .portal_telegram import PortalTelegram

Portal = Union[BasePortal, PortalMatrix, PortalTelegram, PortalMetadata]
