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
from itertools import chain

from alchemysession import AlchemySessionContainer

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
from .portal import init as init_portal
from .puppet import Puppet, init as init_puppet
from .sqlstatestore import SQLStateStore
from .user import User, init as init_user
from .version import version, linkified_version

try:
    import prometheus_client as prometheus
except ImportError:
    prometheus = None


class TelegramBridge(Bridge):
    name = "mautrix-telegram"
    command = "python -m mautrix-telegram"
    description = "A Matrix-Telegram puppeting bridge."
    repo_url = "https://github.com/tulir/mautrix-telegram"
    real_user_content_key = "net.maunium.telegram.puppet"
    version = version
    markdown_version = linkified_version
    config_class = Config
    matrix_class = MatrixHandler
    state_store_class = SQLStateStore

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

        if self.config["metrics.enabled"]:
            if prometheus:
                prometheus.start_http_server(self.config["metrics.listen_port"])
            else:
                self.log.warning("Metrics are enabled in the config, "
                                 "but prometheus_client is not installed.")

    def prepare_bridge(self) -> None:
        self.bot = init_bot(self.config)
        context = Context(self.az, self.config, self.loop, self.session_container, self, self.bot)
        self._prepare_website(context)
        self.matrix = context.mx = MatrixHandler(context)
        self.manhole = None

        init_abstract_user(context)
        init_formatter(context)
        init_portal(context)
        puppet_startup = init_puppet(context)
        user_startup = init_user(context)
        bot_startup = [self.bot.start()] if self.bot else []
        self.startup_actions = chain(puppet_startup, user_startup, bot_startup)

    def prepare_stop(self) -> None:
        for puppet in Puppet.by_custom_mxid.values():
            puppet.stop()
        self.shutdown_actions = (user.stop() for user in User.by_tgid.values())
        if self.manhole:
            self.manhole.close()
            self.manhole = None


TelegramBridge().run()
