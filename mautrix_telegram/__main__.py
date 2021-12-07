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
from typing import Dict, Any
import asyncio
from time import time

import telethon
from telethon import __version__ as __telethon_version__
from alchemysession import AlchemySessionContainer

from mautrix.types import UserID, RoomID
from mautrix.bridge import Bridge
from mautrix.util.db import Base
from mautrix.util.opt_prometheus import Gauge

from .web.provisioning import ProvisioningAPI
from .web.public import PublicBridgeWebsite
from .abstract_user import init as init_abstract_user
from .bot import Bot, init as init_bot
from .config import Config
from .context import Context
from .db import init as init_db
from .db.user_activity import UserActivity
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

ACTIVE_USER_METRICS_INTERVAL_S = 15 * 60 # 15 minutes
METRIC_ACTIVE_PUPPETS = Gauge('bridge_active_puppets_total', 'Number of active Telegram users bridged into Matrix')
METRIC_BLOCKING = Gauge('bridge_blocked', 'Is the bridge currently blocking messages')
METRIC_AS_CONNECTIONS = Gauge('bridge_as_connections', 'Number of active/available TCP connections in Appservice\'s pool', ['status'])
METRIC_BOT_STARTUP_OK = Gauge('bridge_bot_startup_ok', 'Whether or not the configured Telegram started up correctly')

class TelegramBridge(Bridge):
    module = "mautrix_telegram"
    name = "mautrix-telegram"
    command = "python -m mautrix-telegram"
    description = "A Matrix-Telegram puppeting bridge."
    repo_url = "https://github.com/mautrix/telegram"
    real_user_content_key = "net.maunium.telegram.puppet"
    version = version
    markdown_version = linkified_version
    config_class = Config
    matrix_class = MatrixHandler

    config: Config
    context: Context
    session_container: AlchemySessionContainer
    bot: Bot

    periodic_active_metrics_task: asyncio.Task
    is_blocked: bool = False

    periodic_sync_task: asyncio.Task = None
    as_bridge_liveness_task: asyncio.Task = None

    latest_telegram_update_timestamp: float

    as_connection_metric_task: asyncio.Task = None

    def prepare_db(self) -> None:
        super().prepare_db()
        init_db(self.db)
        self.session_container = AlchemySessionContainer(
            engine=self.db, table_base=Base, session=False,
            table_prefix="telethon_", manage_tables=False)

    def _prepare_website(self) -> None:
        if self.config["appservice.public.enabled"]:
            public_website = PublicBridgeWebsite(self.loop)
            self.az.app.add_subapp(self.config["appservice.public.prefix"], public_website.app)
            self.context.public_website = public_website

        if self.config["appservice.provisioning.enabled"]:
            provisioning_api = ProvisioningAPI(self.context)
            self.az.app.add_subapp(self.config["appservice.provisioning.prefix"],
                                   provisioning_api.app)
            self.context.provisioning_api = provisioning_api

    def prepare_bridge(self) -> None:
        self.bot = init_bot(self.config)
        self.context = Context(self.az, self.config, self.loop, self.session_container, self, self.bot)
        self._prepare_website()
        self.matrix = self.context.mx = MatrixHandler(self.context)

        init_abstract_user(self.context)
        init_formatter(self.context)
        init_portal(self.context)
        self.add_startup_actions(init_puppet(self.context))

        if self.config["bridge.resend_bridge_info"]:
            self.add_startup_actions(self.resend_bridge_info())

        # Explicitly not a startup_action, as startup_actions block startup
        if self.config['bridge.limits.enable_activity_tracking'] is not False:
            self.periodic_sync_task = self.loop.create_task(self._loop_active_puppet_metric())

        if self.config.get('telegram.liveness_timeout', 0) >= 1:
            self.as_bridge_liveness_task = self.loop.create_task(self._loop_check_bridge_liveness())

    async def start(self) -> None:
        await super().start()

        if self.config['metrics.enabled']:
            self.as_connection_metric_task = self.loop.create_task(self._loop_check_as_connection_pool())

        if self.bot:
            try:
                await self.bot.start()
                METRIC_BOT_STARTUP_OK.set(1)
            except telethon.errors.RPCError as e:
                self.log.error(f"Failed to start bot: {e}")
                METRIC_BOT_STARTUP_OK.set(0)

        semaphore = None
        concurrency = self.config['telegram.connection.concurrent_connections_startup']
        if concurrency:
            semaphore = asyncio.Semaphore(concurrency)
            await semaphore.acquire()

        async def sem_task(task):
            if not semaphore:
                return await task
            async with semaphore:
                return await task

        await asyncio.gather(*(sem_task(task) for task in init_user(self.context)))

    async def resend_bridge_info(self) -> None:
        self.config["bridge.resend_bridge_info"] = False
        self.config.save()
        self.log.info("Re-sending bridge info state event to all portals")
        for portal in Portal.all():
            await portal.update_bridge_info()
        self.log.info("Finished re-sending bridge info state events")

    def prepare_stop(self) -> None:
        if self.periodic_sync_task:
            self.periodic_sync_task.cancel()
        if self.as_connection_metric_task:
            self.as_connection_metric_task.cancel()
        if self.as_bridge_liveness_task:
            self.as_bridge_liveness_task.cancel()
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

    # The caller confirms that at the time of calling this, the bridge is receiving updates from Telegram.
    # If this function is not called regularly, the bridge may be configured to report this on the /live metric endpoint.
    def confirm_bridge_liveness(self):
        self.latest_telegram_update_timestamp = time()
        self.az.live = True

    async def _update_active_puppet_metric(self) -> None:
        active_users = UserActivity.get_active_count(
            self.config['bridge.limits.min_puppet_activity_days'],
            self.config['bridge.limits.puppet_inactivity_days'],
        )

        block_on_limit_reached = self.config['bridge.limits.block_on_limit_reached']
        max_puppet_limit = self.config['bridge.limits.max_puppet_limit']
        if block_on_limit_reached and max_puppet_limit is not None:
            self.is_blocked = max_puppet_limit < active_users
            METRIC_BLOCKING.set(int(self.is_blocked))
        self.log.debug(f"Current active puppet count is {active_users}")
        METRIC_ACTIVE_PUPPETS.set(active_users)

    async def _loop_active_puppet_metric(self) -> None:
        while True:
            try:
                await asyncio.sleep(ACTIVE_USER_METRICS_INTERVAL_S)
            except asyncio.CancelledError:
                return
            self.log.debug("Executing periodic active puppet metric check")
            try:
                await self._update_active_puppet_metric()
            except asyncio.CancelledError:
                return
            except Exception as e:
                self.log.exception(f"Error while checking: {e}")

    async def _loop_check_as_connection_pool(self) -> None:
        while True:
            try:
                connector = self.az.http_session.connector
                limit = connector.limit
                # a horrible, horrible reach into asyncio.TCPConnector's internal API
                # inspired by its (also private) _available_connections()
                active = len(connector._acquired)

                METRIC_AS_CONNECTIONS.labels('active').set(active)
                METRIC_AS_CONNECTIONS.labels('limit').set(limit)
            except Exception as e:
                self.log.exception(f"Error while checking AS connection pool stats: {e}")

            await asyncio.sleep(15)

    async def _loop_check_bridge_liveness(self) -> None:
        while True:
            if self.latest_telegram_update_timestamp and self.latest_telegram_update_timestamp < time() - self.config.get('telegram.liveness_timeout'):
                self.az.live = False

            await asyncio.sleep(15)

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
