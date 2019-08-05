from typing import Union
from .base import BasePortal
from .portal_matrix import PortalMatrix
from .portal_metadata import PortalMetadata
from .portal_telegram import PortalTelegram

Portal = Union[BasePortal, PortalMatrix, PortalMetadata, PortalTelegram]
