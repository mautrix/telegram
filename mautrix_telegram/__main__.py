# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2019 Tulir Asokan
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
from typing import Optional

from alchemysession import AlchemySessionContainer

from mautrix.types import UserID, RoomID
from mautrix.bridge import Bridge
from mautrix.util.db import Base

from .web.provisioning import ProvisioningAPI
from .web.public import PublicBridgeWebsite
from .commands.manhole import ManholeState
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

try:
    import prometheus_client as prometheus
except ImportError:
    prometheus = None


class TelegramBridge(Bridge):
    module = "mautrix_telegram"
    name = "mautrix-telegram"
    command = "python -m mautrix-telegram"
    description = "A Matrix-Telegram puppeting bridge."
    repo_url = "https://github.com/tulir/mautrix-telegram"
    real_user_content_key = "net.maunium.telegram.puppet"
    version = version
    markdown_version = linkified_version
    config_class = Config
    matrix_class = MatrixHandler

    config: Config
    session_container: AlchemySessionContainer
    bot: Bot
    manhole: Optional[ManholeState]

    def prepare_db(self) -> None:
        super().prepare_db()
        init_db(self.db)
        self.session_container = AlchemySessionContainer(
            engine=self.db, table_base=Base, session=False,
            table_prefix="telethon_", manage_tables=False)

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
        self.manhole = None

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
        if self.manhole:
            self.manhole.close()
            self.manhole = None

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


TelegramBridge().run()
