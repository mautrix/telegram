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
from __future__ import annotations

from typing import Any

from telethon import __version__ as __telethon_version__

from mautrix.bridge import Bridge
from mautrix.types import RoomID, UserID

from .bot import Bot
from .config import Config
from .db import init as init_db, upgrade_table
from .matrix import MatrixHandler
from .portal import Portal
from .puppet import Puppet
from .user import User
from .version import linkified_version, version
from .web.provisioning import ProvisioningAPI
from .web.public import PublicBridgeWebsite

from .abstract_user import AbstractUser  # isort: skip


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
    upgrade_table = upgrade_table

    config: Config
    bot: Bot | None
    public_website: PublicBridgeWebsite | None
    provisioning_api: ProvisioningAPI | None

    def prepare_db(self) -> None:
        super().prepare_db()
        init_db(self.db)

    def _prepare_website(self) -> None:
        if self.config["appservice.provisioning.enabled"]:
            self.provisioning_api = ProvisioningAPI(self)
            self.az.app.add_subapp(
                self.config["appservice.provisioning.prefix"], self.provisioning_api.app
            )
        else:
            self.provisioning_api = None

        if self.config["appservice.public.enabled"]:
            self.public_website = PublicBridgeWebsite(self.loop)
            self.az.app.add_subapp(
                self.config["appservice.public.prefix"], self.public_website.app
            )
        else:
            self.public_website = None

    def prepare_bridge(self) -> None:
        self._prepare_website()
        AbstractUser.init_cls(self)
        bot_token: str = self.config["telegram.bot_token"]
        if bot_token and not bot_token.lower().startswith("disable"):
            self.bot = AbstractUser.relaybot = Bot(bot_token)
        else:
            self.bot = AbstractUser.relaybot = None
        self.matrix = MatrixHandler(self)
        Portal.init_cls(self)
        self.add_startup_actions(Puppet.init_cls(self))
        self.add_startup_actions(User.init_cls(self))
        self.add_startup_actions(Portal.restart_scheduled_disappearing())
        if self.bot:
            self.add_startup_actions(self.bot.start())
        if self.config["bridge.resend_bridge_info"]:
            self.add_startup_actions(self.resend_bridge_info())

    async def resend_bridge_info(self) -> None:
        self.config["bridge.resend_bridge_info"] = False
        self.config.save()
        self.log.info("Re-sending bridge info state event to all portals")
        async for portal in Portal.all():
            await portal.update_bridge_info()
        self.log.info("Finished re-sending bridge info state events")

    def prepare_stop(self) -> None:
        for puppet in Puppet.by_custom_mxid.values():
            puppet.stop()
        self.add_shutdown_actions(user.stop() for user in User.by_tgid.values())
        if self.bot:
            self.add_shutdown_actions(self.bot.stop())

    async def get_user(self, user_id: UserID, create: bool = True) -> User | None:
        user = await User.get_by_mxid(user_id, create=create)
        if user:
            await user.ensure_started()
        return user

    async def get_portal(self, room_id: RoomID) -> Portal | None:
        return await Portal.get_by_mxid(room_id)

    async def get_puppet(self, user_id: UserID, create: bool = False) -> Puppet | None:
        return await Puppet.get_by_mxid(user_id, create=create)

    async def get_double_puppet(self, user_id: UserID) -> Puppet | None:
        return await Puppet.get_by_custom_mxid(user_id)

    def is_bridge_ghost(self, user_id: UserID) -> bool:
        return bool(Puppet.get_id_from_mxid(user_id))

    async def count_logged_in_users(self) -> int:
        return len([user for user in User.by_tgid.values() if user.tgid])

    async def manhole_global_namespace(self, user_id: UserID) -> dict[str, Any]:
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
