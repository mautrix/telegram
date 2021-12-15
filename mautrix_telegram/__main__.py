# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2021 Tulir Asokan
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
from typing import Dict, Any
import sys

from telethon import __version__ as __telethon_version__
from alchemysession import AlchemySessionContainer

from mautrix.types import UserID, RoomID
from mautrix.bridge import Bridge
from mautrix.util.db import Base
from mautrix.bridge.state_store.sqlalchemy import SQLBridgeStateStore

from .web.provisioning import ProvisioningAPI
from .web.public import PublicBridgeWebsite
from .abstract_user import init as init_abstract_user
from .bot import Bot, init as init_bot
from .config import Config
from .context import Context
from .db import init as init_db
from .formatter import init as init_formatter
from .matrix import MatrixHandler
from .portal import Portal, init as init_portal
from .puppet import Puppet, init as init_puppet
from .user import User, init as init_user
from .version import version, linkified_version

import sqlalchemy as sql
from sqlalchemy.engine.base import Engine

try:
    import prometheus_client as prometheus
except ImportError:
    prometheus = None


class TelegramBridge(Bridge):
    module = "mautrix_telegram"
    name = "mautrix-telegram"
    command = "python -m mautrix-telegram"
    description = "A Matrix-Telegram puppeting bridge."
    repo_url = "https://github.com/mautrix/telegram"
    version = version
    markdown_version = linkified_version
    config_class = Config
    matrix_class = MatrixHandler
    state_store_class = SQLBridgeStateStore

    db: 'Engine'
    config: Config
    session_container: AlchemySessionContainer
    bot: Bot

    def prepare_db(self) -> None:
        if not sql:
            raise RuntimeError("SQLAlchemy is not installed")
        self.db = sql.create_engine(self.config["appservice.database"],
                                    **self.config["appservice.database_opts"])
        Base.metadata.bind = self.db
        if not self.db.has_table("alembic_version"):
            self.log.critical("alembic_version table not found. "
                              "Did you forget to `alembic upgrade head`?")
            sys.exit(10)

        init_db(self.db)
        self.session_container = AlchemySessionContainer(
            engine=self.db, table_base=Base, session=False,
            table_prefix="telethon_", manage_tables=False)

    def make_state_store(self) -> None:
        self.state_store = self.state_store_class(self.get_puppet, self.get_double_puppet)

    def _prepare_website(self, context: Context) -> None:
        if self.config["appservice.public.enabled"]:
            public_website = PublicBridgeWebsite(self.loop)
            self.az.app.add_subapp(self.config["appservice.public.prefix"], public_website.app)
            context.public_website = public_website

        if self.config["appservice.provisioning.enabled"]:
            provisioning_api = ProvisioningAPI(context)
            self.az.app.add_subapp(self.config["appservice.provisioning.prefix"],
                                   provisioning_api.app)
            context.provisioning_api = provisioning_api

    def prepare_bridge(self) -> None:
        self.bot = init_bot(self.config)
        context = Context(self.az, self.config, self.loop, self.session_container, self, self.bot)
        self._prepare_website(context)
        self.matrix = context.mx = MatrixHandler(context)

        init_abstract_user(context)
        init_formatter(context)
        init_portal(context)
        self.add_startup_actions(init_puppet(context))
        self.add_startup_actions(init_user(context))
        if self.bot:
            self.add_startup_actions(self.bot.start())
        if self.config["bridge.resend_bridge_info"]:
            self.add_startup_actions(self.resend_bridge_info())

    async def resend_bridge_info(self) -> None:
        self.config["bridge.resend_bridge_info"] = False
        self.config.save()
        self.log.info("Re-sending bridge info state event to all portals")
        for portal in Portal.all():
            await portal.update_bridge_info()
        self.log.info("Finished re-sending bridge info state events")

    def prepare_stop(self) -> None:
        for puppet in Puppet.by_custom_mxid.values():
            puppet.stop()
        self.shutdown_actions = (user.stop() for user in User.by_tgid.values())

    async def get_user(self, user_id: UserID, create: bool = True) -> User:
        user = User.get_by_mxid(user_id, create=create)
        if user:
            await user.ensure_started()
        return user

    async def get_portal(self, room_id: RoomID) -> Portal:
        return Portal.get_by_mxid(room_id)

    async def get_puppet(self, user_id: UserID, create: bool = False) -> Puppet:
        return await Puppet.get_by_mxid(user_id, create=create)

    async def get_double_puppet(self, user_id: UserID) -> Puppet:
        return await Puppet.get_by_custom_mxid(user_id)

    def is_bridge_ghost(self, user_id: UserID) -> bool:
        return bool(Puppet.get_id_from_mxid(user_id))

    async def count_logged_in_users(self) -> int:
        return len([user for user in User.by_tgid.values() if user.tgid])

    async def manhole_global_namespace(self, user_id: UserID) -> Dict[str, Any]:
        return {
            **await super().manhole_global_namespace(user_id),
            "User": User,
            "Portal": Portal,
            "Puppet": Puppet,
        }

    @property
    def manhole_banner_program_version(self) -> str:
        return f"{super().manhole_banner_program_version} and Telethon {__telethon_version__}"


TelegramBridge().run()
