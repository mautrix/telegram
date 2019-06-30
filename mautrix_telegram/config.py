# -*- coding: future_fstrings -*-
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
from typing import Any, Dict, Optional, Tuple
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
import random
import string

yaml = YAML()  # type: YAML
yaml.indent(4)


class DictWithRecursion:
    def __init__(self, data: Optional[CommentedMap] = None) -> None:
        self._data = data or CommentedMap()  # type: CommentedMap

    @staticmethod
    def _parse_key(key: str) -> Tuple[str, Optional[str]]:
        if '.' not in key:
            return key, None
        key, next_key = key.split('.', 1)
        if len(key) > 0 and key[0] == "[":
            end_index = next_key.index("]")
            key = key[1:] + "." + next_key[:end_index]
            next_key = next_key[end_index + 2:] if len(next_key) > end_index + 1 else None
        return key, next_key

    def _recursive_get(self, data: CommentedMap, key: str, default_value: Any) -> Any:
        key, next_key = self._parse_key(key)
        if next_key is not None:
            next_data = data.get(key, CommentedMap())
            return self._recursive_get(next_data, next_key, default_value)
        return data.get(key, default_value)

    def get(self, key: str, default_value: Any, allow_recursion: bool = True) -> Any:
        if allow_recursion and '.' in key:
            return self._recursive_get(self._data, key, default_value)
        return self._data.get(key, default_value)

    def __getitem__(self, key: str) -> Any:
        return self.get(key, None)

    def __contains__(self, key: str) -> bool:
        return self[key] is not None

    def _recursive_set(self, data: CommentedMap, key: str, value: Any) -> None:
        key, next_key = self._parse_key(key)
        if next_key is not None:
            if key not in data:
                data[key] = CommentedMap()
            next_data = data.get(key, CommentedMap())
            return self._recursive_set(next_data, next_key, value)
        data[key] = value

    def set(self, key: str, value: Any, allow_recursion: bool = True) -> None:
        if allow_recursion and '.' in key:
            self._recursive_set(self._data, key, value)
            return
        self._data[key] = value

    def __setitem__(self, key: str, value: Any) -> None:
        self.set(key, value)

    def _recursive_del(self, data: CommentedMap, key: str) -> None:
        key, next_key = self._parse_key(key)
        if next_key is not None:
            if key not in data:
                return
            next_data = data[key]
            return self._recursive_del(next_data, next_key)
        try:
            del data[key]
            del data.ca.items[key]
        except KeyError:
            pass

    def delete(self, key: str, allow_recursion: bool = True) -> None:
        if allow_recursion and '.' in key:
            self._recursive_del(self._data, key)
            return
        try:
            del self._data[key]
            del self._data.ca.items[key]
        except KeyError:
            pass

    def __delitem__(self, key: str) -> None:
        self.delete(key)


class Config(DictWithRecursion):
    def __init__(self, path: str, registration_path: str, base_path: str,
                 overrides: Dict[str, Any] = None) -> None:
        super().__init__()
        self.path = path  # type: str
        self.registration_path = registration_path  # type: str
        self.base_path = base_path  # type: str
        self._registration = None  # type: Optional[Dict]
        self._overrides = overrides or {}  # type: Dict[str, Any]

    def __getitem__(self, key: str) -> Any:
        try:
            return self._overrides[f"MAUTRIX_TELEGRAM_{key.replace('.', '_').upper()}"]
        except KeyError:
            return super().__getitem__(key)

    def load(self) -> None:
        with open(self.path, 'r') as stream:
            self._data = yaml.load(stream)

    def load_base(self) -> Optional[DictWithRecursion]:
        try:
            with open(self.base_path, 'r') as stream:
                return DictWithRecursion(yaml.load(stream))
        except OSError:
            pass
        return None

    def save(self) -> None:
        with open(self.path, 'w') as stream:
            yaml.dump(self._data, stream)
        if self._registration and self.registration_path:
            with open(self.registration_path, 'w') as stream:
                yaml.dump(self._registration, stream)

    @staticmethod
    def _new_token() -> str:
        return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(64))

    def update(self) -> None:
        base = self.load_base()
        if not base:
            return

        def copy(from_path, to_path=None) -> None:
            if from_path in self:
                base[to_path or from_path] = self[from_path]

        def copy_dict(from_path, to_path=None, override_existing_map=True) -> None:
            if from_path in self:
                to_path = to_path or from_path
                if override_existing_map or to_path not in base:
                    base[to_path] = CommentedMap()
                for key, value in self[from_path].items():
                    base[to_path][key] = value

        copy("homeserver.address")
        copy("homeserver.domain")
        copy("homeserver.verify_ssl")

        if "appservice.protocol" in self and "appservice.address" not in self:
            protocol, hostname, port = (self["appservice.protocol"], self["appservice.hostname"],
                                        self["appservice.port"])
            base["appservice.address"] = f"{protocol}://{hostname}:{port}"
        else:
            copy("appservice.address")
        copy("appservice.hostname")
        copy("appservice.port")
        copy("appservice.max_body_size")

        copy("appservice.database")

        copy("appservice.public.enabled")
        copy("appservice.public.prefix")
        copy("appservice.public.external")

        copy("appservice.provisioning.enabled")
        copy("appservice.provisioning.prefix")
        copy("appservice.provisioning.shared_secret")
        if base["appservice.provisioning.shared_secret"] == "generate":
            base["appservice.provisioning.shared_secret"] = self._new_token()

        copy("appservice.id")
        copy("appservice.bot_username")
        copy("appservice.bot_displayname")
        copy("appservice.bot_avatar")

        copy("appservice.community_id")

        copy("appservice.as_token")
        copy("appservice.hs_token")

        copy("metrics.enabled")
        copy("metrics.listen_port")

        copy("bridge.username_template")
        copy("bridge.alias_template")
        copy("bridge.displayname_template")

        copy("bridge.displayname_preference")

        copy("bridge.max_initial_member_sync")
        copy("bridge.sync_channel_members")
        copy("bridge.skip_deleted_members")
        copy("bridge.startup_sync")
        copy("bridge.sync_dialog_limit")
        copy("bridge.max_telegram_delete")
        copy("bridge.sync_matrix_state")
        copy("bridge.allow_matrix_login")
        copy("bridge.plaintext_highlights")
        copy("bridge.public_portals")
        copy("bridge.catch_up")
        copy("bridge.sync_with_custom_puppets")
        copy("bridge.telegram_link_preview")
        copy("bridge.inline_images")
        copy("bridge.image_as_file_size")
        copy("bridge.max_document_size")

        copy("bridge.bot_messages_as_notices")
        if isinstance(self["bridge.bridge_notices"], bool):
            base["bridge.bridge_notices"] = {
                "default": self["bridge.bridge_notices"],
                "exceptions": ["@importantbot:example.com"],
            }
        else:
            copy("bridge.bridge_notices")

        copy("bridge.deduplication.pre_db_check")
        copy("bridge.deduplication.cache_queue_length")

        if "bridge.message_formats.m_text" in self:
            del self["bridge.message_formats"]
        copy_dict("bridge.message_formats", override_existing_map=False)

        copy("bridge.state_event_formats.join")
        copy("bridge.state_event_formats.leave")
        copy("bridge.state_event_formats.name_change")

        copy("bridge.filter.mode")
        copy("bridge.filter.list")

        copy("bridge.command_prefix")

        migrate_permissions = ("bridge.permissions" not in self
                               or "bridge.whitelist" in self
                               or "bridge.admins" in self)
        if migrate_permissions:
            permissions = self["bridge.permissions"] or CommentedMap()
            for entry in self["bridge.whitelist"] or []:
                permissions[entry] = "full"
            for entry in self["bridge.admins"] or []:
                permissions[entry] = "admin"
            base["bridge.permissions"] = permissions
        else:
            copy_dict("bridge.permissions")

        if "bridge.relaybot" not in self:
            copy("bridge.authless_relaybot_portals", "bridge.relaybot.authless_portals")
        else:
            copy("bridge.relaybot.authless_portals")
            copy("bridge.relaybot.whitelist_group_admins")
            copy("bridge.relaybot.whitelist")
            copy("bridge.relaybot.ignore_own_incoming_events")

        copy("telegram.api_id")
        copy("telegram.api_hash")
        copy("telegram.bot_token")

        copy("telegram.connection.timeout")
        copy("telegram.connection.retries")
        copy("telegram.connection.retry_delay")
        copy("telegram.connection.flood_sleep_threshold")
        copy("telegram.connection.request_retries")

        copy("telegram.device_info.device_model")
        copy("telegram.device_info.system_version")
        copy("telegram.device_info.app_version")
        copy("telegram.device_info.lang_code")
        copy("telegram.device_info.system_lang_code")

        copy("telegram.server.enabled")
        copy("telegram.server.dc")
        copy("telegram.server.ip")
        copy("telegram.server.port")

        copy("telegram.proxy.type")
        copy("telegram.proxy.address")
        copy("telegram.proxy.port")
        copy("telegram.proxy.rdns")
        copy("telegram.proxy.username")
        copy("telegram.proxy.password")

        if "appservice.debug" in self and "logging" not in self:
            level = "DEBUG" if self["appservice.debug"] else "INFO"
            base["logging.root.level"] = level
            base["logging.loggers.mau.level"] = level
            base["logging.loggers.telethon.level"] = level
        else:
            copy("logging")

        self._data = base._data
        self.save()

    def _get_permissions(self, key: str) -> Tuple[bool, bool, bool, bool, bool, bool]:
        level = self["bridge.permissions"].get(key, "")
        admin = level == "admin"
        matrix_puppeting = level == "full" or admin
        puppeting = level == "puppeting" or matrix_puppeting
        user = level == "user" or puppeting
        relaybot = level == "relaybot" or user
        return relaybot, user, puppeting, matrix_puppeting, admin, level

    def get_permissions(self, mxid: str) -> Tuple[bool, bool, bool, bool, bool, bool]:
        permissions = self["bridge.permissions"] or {}
        if mxid in permissions:
            return self._get_permissions(mxid)

        homeserver = mxid[mxid.index(":") + 1:]
        if homeserver in permissions:
            return self._get_permissions(homeserver)

        return self._get_permissions("*")

    def generate_registration(self) -> None:
        homeserver = self["homeserver.domain"]

        username_format = self.get("bridge.username_template", "telegram_{userid}") \
            .format(userid=".+")
        alias_format = self.get("bridge.alias_template", "telegram_{groupname}") \
            .format(groupname=".+")

        self.set("appservice.as_token", self._new_token())
        self.set("appservice.hs_token", self._new_token())

        self._registration = {
            "id": self["appservice.id"] or "telegram",
            "as_token": self["appservice.as_token"],
            "hs_token": self["appservice.hs_token"],
            "namespaces": {
                "users": [{
                    "exclusive": True,
                    "regex": f"@{username_format}:{homeserver}"
                }],
                "aliases": [{
                    "exclusive": True,
                    "regex": f"#{alias_format}:{homeserver}"
                }]
            },
            "url": self["appservice.address"],
            "sender_localpart": self["appservice.bot_username"],
            "rate_limited": False
        }
        if self["appservice.community_id"]:
            self._registration["namespaces"]["users"][0]["group_id"] \
                = self["appservice.community_id"]
