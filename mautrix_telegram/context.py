# -*- coding: future_fstrings -*-
# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2018 Tulir Asokan
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
from typing import Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    import asyncio

    from sqlalchemy.orm import scoped_session

    from alchemysession import AlchemySessionContainer
    from mautrix_appservice import AppService

    from .web import PublicBridgeWebsite, ProvisioningAPI
    from .config import Config
    from .bot import Bot
    from .matrix import MatrixHandler


class Context:
    def __init__(self, az: "AppService", db: "scoped_session", config: "Config",
                 loop: "asyncio.AbstractEventLoop", session_container: "AlchemySessionContainer"
                 ) -> None:
        self.az = az  # type: AppService
        self.db = db  # type: scoped_session
        self.config = config  # type: Config
        self.loop = loop  # type: asyncio.AbstractEventLoop
        self.bot = None  # type: Optional[Bot]
        self.mx = None  # type: MatrixHandler
        self.session_container = session_container  # type: AlchemySessionContainer
        self.public_website = None  # type: PublicBridgeWebsite
        self.provisioning_api = None  # type: ProvisioningAPI

    @property
    def core(self) -> Tuple['AppService', 'scoped_session', 'Config',
                            'asyncio.AbstractEventLoop', Optional['Bot']]:
        return (self.az, self.db, self.config, self.loop, self.bot)
