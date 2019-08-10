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
from mautrix.types import UserID
from mautrix.bridge.db import SQLStateStore as BaseSQLStateStore

from . import puppet as pu


class SQLStateStore(BaseSQLStateStore):
    def is_registered(self, user_id: UserID) -> bool:
        puppet = pu.Puppet.get_by_mxid(user_id, create=False)
        if puppet:
            return puppet.is_registered
        custom_puppet = pu.Puppet.get_by_custom_mxid(user_id)
        if custom_puppet:
            return True
        return super().is_registered(user_id)

    def registered(self, user_id: UserID) -> None:
        puppet = pu.Puppet.get_by_mxid(user_id, create=True)
        if puppet:
            puppet.is_registered = True
            puppet.save()
        else:
            super().registered(user_id)
