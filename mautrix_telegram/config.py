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
import ruamel.yaml
import random
import string

yaml = ruamel.yaml.YAML()

class DictWithRecursion:
    def __init__(self, data={}):
        self._data = data

    def _recursive_get(self, data, key, default_value):
        if '.' in key:
            key, next_key = key.split('.', 1)
            next_data = data.get(key, {})
            return self._recursive_get(next_data, next_key, default_value)
        return data.get(key, default_value)

    def get(self, key, default_value, allow_recursion=True):
        if allow_recursion and '.' in key:
            return self._recursive_get(self._data, key, default_value)
        return self._data.get(key, default_value)

    def __getitem__(self, key):
        return self.get(key, None)

    def _recursive_set(self, data, key, value):
        if '.' in key:
            key, next_key = key.split('.', 1)
            if key not in data:
                data[key] = {}
            next_data = data.get(key, {})
            self._recursive_set(next_data, next_key, value)
            return
        data[key] = value

    def set(self, key, value, allow_recursion=True):
        if allow_recursion and '.' in key:
            self._recursive_set(self._data, key, value)
            return
        self._data[key] = value

    def __setitem__(self, key, value):
        self.set(key, value)


class Config(DictWithRecursion):
    def __init__(self, path, registration_path):
        super().__init__()
        self.path = path
        self.registration_path = registration_path
        self._registration = None

    def load(self):
        with open(self.path, 'r') as stream:
            self._data = yaml.load(stream)

    def save(self):
        with open(self.path, 'w') as stream:
            yaml.dump(self._data, stream)
        if self._registration and self.registration_path:
            with open(self.registration_path, 'w') as stream:
                yaml.dump(self._registration, stream)

    def _new_token(self):
        return "".join(random.choices(string.ascii_lowercase + string.digits, k=64))

    def generate_registration(self):
        homeserver = self["homeserver.domain"]

        username_format = self.get("bridge.username_template", "telegram_{}").format(".+")
        alias_format = self.get("bridge.alias_template", "telegram_{}").format(".+")

        self.set("appservice.as_token", self._new_token())
        self.set("appservice.hs_token", self._new_token())

        appservice = self.get("appservice", {})
        self._registration = {
            "id": appservice.get("id", "telegram"),
            "as_token": appservice.get("as_token"),
            "hs_token": appservice.get("hs_token"),
            "namespaces": {
                "users": [{
                    "exclusive": True,
                    "regex": f"@{username_format}:{homeserver}"
                }],
                "aliases": [{
                    "exclusive": True,
                    "regex": f"@{alias_format}:{homeserver}"
                }]
            },
            "url": f"{appservice.get('protocol')}://{appservice.get('hostname')}:{appservice.get('port')}",
            "sender_localpart": appservice.get("bot_username"),
            "rate_limited": False
        }
