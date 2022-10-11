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
from typing import Any, List, NamedTuple
import os

from ruamel.yaml.comments import CommentedMap

from mautrix.bridge.config import BaseBridgeConfig
from mautrix.client import Client
from mautrix.types import UserID
from mautrix.util.config import ConfigUpdateHelper, ForbiddenDefault, ForbiddenKey

Permissions = NamedTuple(
    "Permissions",
    relaybot=bool,
    user=bool,
    puppeting=bool,
    matrix_puppeting=bool,
    admin=bool,
    level=str,
)


class Config(BaseBridgeConfig):
    @property
    def forbidden_defaults(self) -> List[ForbiddenDefault]:
        return [
            *super().forbidden_defaults,
            ForbiddenDefault(
                "appservice.database",
                "postgres://username:password@hostname/dbname",
            ),
            ForbiddenDefault(
                "appservice.public.external",
                "https://example.com/public",
                condition="appservice.public.enabled",
            ),
            ForbiddenDefault("bridge.permissions", ForbiddenKey("example.com")),
            ForbiddenDefault("telegram.api_id", 12345),
            ForbiddenDefault("telegram.api_hash", "tjyd5yge35lbodk1xwzw2jstp90k55qz"),
        ]

    def do_update(self, helper: ConfigUpdateHelper) -> None:
        super().do_update(helper)
        copy, copy_dict, base = helper

        if "appservice.protocol" in self and "appservice.address" not in self:
            protocol, hostname, port = (
                self["appservice.protocol"],
                self["appservice.hostname"],
                self["appservice.port"],
            )
            base["appservice.address"] = f"{protocol}://{hostname}:{port}"
        if "appservice.debug" in self and "logging" not in self:
            level = "DEBUG" if self["appservice.debug"] else "INFO"
            base["logging.root.level"] = level
            base["logging.loggers.mau.level"] = level
            base["logging.loggers.telethon.level"] = level

        copy("appservice.public.enabled")
        copy("appservice.public.prefix")
        copy("appservice.public.external")

        copy("appservice.provisioning.enabled")
        copy("appservice.provisioning.prefix")
        if base["appservice.provisioning.prefix"].endswith("/v1"):
            base["appservice.provisioning.prefix"] = base["appservice.provisioning.prefix"][
                : -len("/v1")
            ]
        copy("appservice.provisioning.shared_secret")
        if base["appservice.provisioning.shared_secret"] == "generate":
            base["appservice.provisioning.shared_secret"] = self._new_token()

        if "pool_size" in base["appservice.database_opts"]:
            pool_size = base["appservice.database_opts"].pop("pool_size")
            base["appservice.database_opts.min_size"] = pool_size
            base["appservice.database_opts.max_size"] = pool_size
        if "pool_pre_ping" in base["appservice.database_opts"]:
            del base["appservice.database_opts.pool_pre_ping"]

        copy("metrics.enabled")
        copy("metrics.listen_port")

        copy("bridge.username_template")
        copy("bridge.alias_template")
        copy("bridge.displayname_template")

        copy("bridge.displayname_preference")
        copy("bridge.displayname_max_length")
        copy("bridge.allow_avatar_remove")

        copy("bridge.max_initial_member_sync")
        copy("bridge.max_member_count")
        copy("bridge.sync_channel_members")
        copy("bridge.skip_deleted_members")
        copy("bridge.startup_sync")
        if "bridge.sync_dialog_limit" in self:
            base["bridge.sync_create_limit"] = self["bridge.sync_dialog_limit"]
            base["bridge.sync_update_limit"] = self["bridge.sync_dialog_limit"]
        else:
            copy("bridge.sync_update_limit")
            copy("bridge.sync_create_limit")
        copy("bridge.sync_direct_chats")
        copy("bridge.max_telegram_delete")
        copy("bridge.sync_matrix_state")
        copy("bridge.allow_matrix_login")
        copy("bridge.public_portals")
        copy("bridge.sync_with_custom_puppets")
        copy("bridge.sync_direct_chat_list")
        copy("bridge.double_puppet_server_map")
        copy("bridge.double_puppet_allow_discovery")
        copy("bridge.create_group_on_invite")
        if "bridge.login_shared_secret" in self:
            base["bridge.login_shared_secret_map"] = {
                base["homeserver.domain"]: self["bridge.login_shared_secret"]
            }
        else:
            copy("bridge.login_shared_secret_map")
        copy("bridge.telegram_link_preview")
        copy("bridge.invite_link_resolve")
        copy("bridge.caption_in_message")
        copy("bridge.image_as_file_size")
        copy("bridge.image_as_file_pixels")
        copy("bridge.parallel_file_transfer")
        copy("bridge.federate_rooms")
        copy("bridge.always_custom_emoji_reaction")
        copy("bridge.animated_sticker.target")
        copy("bridge.animated_sticker.convert_from_webm")
        copy("bridge.animated_sticker.args.width")
        copy("bridge.animated_sticker.args.height")
        copy("bridge.animated_sticker.args.fps")
        copy("bridge.animated_emoji.target")
        copy("bridge.animated_emoji.args.width")
        copy("bridge.animated_emoji.args.height")
        copy("bridge.animated_emoji.args.fps")
        copy("bridge.private_chat_portal_meta")
        copy("bridge.delivery_receipts")
        copy("bridge.delivery_error_reports")
        copy("bridge.message_status_events")
        copy("bridge.resend_bridge_info")
        copy("bridge.mute_bridging")
        copy("bridge.pinned_tag")
        copy("bridge.archive_tag")
        copy("bridge.tag_only_on_create")
        copy("bridge.bridge_matrix_leave")
        copy("bridge.kick_on_logout")
        copy("bridge.always_read_joined_telegram_notice")
        copy("bridge.backfill.enable")
        copy("bridge.backfill.msc2716")
        copy("bridge.backfill.double_puppet_backfill")
        copy("bridge.backfill.normal_groups")
        copy("bridge.backfill.forward.initial_limit")
        copy("bridge.backfill.forward.sync_limit")
        copy("bridge.backfill.incremental.messages_per_batch")
        copy("bridge.backfill.incremental.post_batch_delay")
        copy("bridge.backfill.incremental.max_batches.user")
        copy("bridge.backfill.incremental.max_batches.normal_group")
        copy("bridge.backfill.incremental.max_batches.supergroup")
        copy("bridge.backfill.incremental.max_batches.channel")

        copy("bridge.initial_power_level_overrides.group")
        copy("bridge.initial_power_level_overrides.user")

        copy("bridge.bot_messages_as_notices")
        if isinstance(self["bridge.bridge_notices"], bool):
            base["bridge.bridge_notices"]["default"] = self["bridge.bridge_notices"]
        else:
            copy("bridge.bridge_notices.default")
            copy("bridge.bridge_notices.exceptions")

        if "bridge.message_formats.m_text" in self:
            del self["bridge.message_formats"]
        copy_dict("bridge.message_formats", override_existing_map=False)
        copy("bridge.emote_format")
        copy("bridge.relay_user_distinguishers")

        copy("bridge.state_event_formats.join")
        copy("bridge.state_event_formats.leave")
        copy("bridge.state_event_formats.name_change")

        copy("bridge.filter.mode")
        copy("bridge.filter.list")

        copy("bridge.command_prefix")

        migrate_permissions = (
            "bridge.permissions" not in self
            or "bridge.whitelist" in self
            or "bridge.admins" in self
        )
        if migrate_permissions:
            permissions = self["bridge.permissions"] or CommentedMap()
            for entry in self["bridge.whitelist"] or []:
                permissions[entry] = "full"
            for entry in self["bridge.admins"] or []:
                permissions[entry] = "admin"
            base["bridge.permissions"] = permissions
        else:
            copy_dict("bridge.permissions", override_existing_map=True)

        if "bridge.relaybot" not in self:
            copy("bridge.authless_relaybot_portals", "bridge.relaybot.authless_portals")
        else:
            copy("bridge.relaybot.private_chat.invite")
            copy("bridge.relaybot.private_chat.state_changes")
            copy("bridge.relaybot.private_chat.message")
            copy("bridge.relaybot.group_chat_invite")
            copy("bridge.relaybot.ignore_unbridged_group_chat")
            copy("bridge.relaybot.authless_portals")
            copy("bridge.relaybot.whitelist_group_admins")
            copy("bridge.relaybot.whitelist")
            copy("bridge.relaybot.ignore_own_incoming_events")

        copy("telegram.api_id")
        copy("telegram.api_hash")
        copy("telegram.bot_token")

        copy("telegram.catch_up")
        copy("telegram.sequential_updates")
        copy("telegram.exit_on_update_error")

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

    def _get_permissions(self, key: str) -> Permissions:
        level = self["bridge.permissions"].get(key, "")
        admin = level == "admin"
        matrix_puppeting = level == "full" or admin
        puppeting = level == "puppeting" or matrix_puppeting
        user = level == "user" or puppeting
        relaybot = level == "relaybot" or user
        return Permissions(relaybot, user, puppeting, matrix_puppeting, admin, level)

    def get_permissions(self, mxid: UserID) -> Permissions:
        permissions = self["bridge.permissions"]
        if mxid in permissions:
            return self._get_permissions(mxid)

        _, homeserver = Client.parse_user_id(mxid)
        if homeserver in permissions:
            return self._get_permissions(homeserver)

        return self._get_permissions("*")
