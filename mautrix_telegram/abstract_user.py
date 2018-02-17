# -*- coding: future_fstrings -*-
# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2018 Tulir Asokan
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
import platform
import os

from .tgclient import MautrixTelegramClient
from . import __version__
from telethon.tl.types import *

config = None


class AbstractUser:
    loop = None
    log = None
    db = None
    az = None

    def __init__(self):
        self.connected = False
        self.whitelisted = False
        self.client = None
        self.tgid = None

    def _init_client(self):
        self.log.debug(f"Initializing client for {self.name}")
        device = f"{platform.system()} {platform.release()}"
        sysversion = MautrixTelegramClient.__version__
        self.client = MautrixTelegramClient(self.name,
                                            config["telegram.api_id"],
                                            config["telegram.api_hash"],
                                            loop=self.loop,
                                            app_version=__version__,
                                            system_version=sysversion,
                                            device_model=device)
        self.client.add_update_handler(self._update_catch)

    async def update(self, update):
        raise NotImplementedError()

    async def post_login(self):
        raise NotImplementedError()

    async def _update_catch(self, update):
        try:
            await self.update(update)
        except Exception:
            self.log.exception("Failed to handle Telegram update")

    async def _get_dialogs(self, limit=None):
        dialogs = await self.client.get_dialogs(limit=limit)
        return [dialog.entity for dialog in dialogs if (
            not isinstance(dialog.entity, (User, ChatForbidden, ChannelForbidden))
            and not (isinstance(dialog.entity, Chat)
                     and (dialog.entity.deactivated or dialog.entity.left)))]

    @property
    def name(self):
        raise NotImplementedError()

    @property
    def logged_in(self):
        return self.client and self.client.is_user_authorized()

    @property
    def has_full_access(self):
        return self.logged_in and self.whitelisted

    async def start(self):
        self.connected = await self.client.connect()

    async def ensure_started(self, even_if_no_session=False):
        if not self.whitelisted:
            return self
        elif not self.connected and (even_if_no_session or os.path.exists(f"{self.name}.session")):
            return await self.start()
        return self

    def stop(self):
        self.client.disconnect()
        self.client = None
        self.connected = False


def init(context):
    global config
    AbstractUser.az, AbstractUser.db, config, AbstractUser.loop, _ = context
