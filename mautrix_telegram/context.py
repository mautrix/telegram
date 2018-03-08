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


class Context:
    def __init__(self, az, db, config, loop, bot, mx, telethon_session_container):
        self.az = az
        self.db = db
        self.config = config
        self.loop = loop
        self.bot = bot
        self.mx = mx
        self.telethon_session_container = telethon_session_container

    def __iter__(self):
        yield self.az
        yield self.db
        yield self.config
        yield self.loop
        yield self.bot
