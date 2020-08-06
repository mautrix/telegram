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
from typing import Awaitable, Any
from io import StringIO

from ruamel.yaml import YAMLError

from mautrix.util.config import yaml
from mautrix.types import EventID

from ... import portal as po, util
from .. import command_handler, CommandEvent, SECTION_PORTAL_MANAGEMENT


@command_handler(needs_auth=False, help_section=SECTION_PORTAL_MANAGEMENT,
                 help_text="View or change per-portal settings.",
                 help_args="<`help`|_subcommand_> [...]")
async def config(evt: CommandEvent) -> None:
    cmd = evt.args[0].lower() if len(evt.args) > 0 else "help"
    if cmd not in ("view", "defaults", "set", "unset", "add", "del"):
        await config_help(evt)
        return
    elif cmd == "defaults":
        await config_defaults(evt)
        return

    portal = po.Portal.get_by_mxid(evt.room_id)
    if not portal:
        await evt.reply("This is not a portal room.")
        return
    elif cmd == "view":
        await config_view(evt, portal)
        return

    if not await portal.can_user_perform(evt.sender, "config"):
        await evt.reply("You do not have the permissions to configure this room.")
        return

    key = evt.args[1] if len(evt.args) > 1 else None
    try:
        value = yaml.load(" ".join(evt.args[2:])) if len(evt.args) > 2 else None
    except YAMLError as e:
        await evt.reply(f"Invalid value provided. Values must be valid YAML.\n{e}")
        return
    if cmd == "set":
        await config_set(evt, portal, key, value)
    elif cmd == "unset":
        await config_unset(evt, portal, key)
    elif cmd == "add" or cmd == "del":
        await config_add_del(evt, portal, key, value, cmd)
    else:
        return
    await portal.save()


def config_help(evt: CommandEvent) -> Awaitable[EventID]:
    return evt.reply("""**Usage:** `$cmdprefix config <subcommand> [...]`. Subcommands:

* **help** - View this help text.
* **view** - View the current config data.
* **defaults** - View the default config values.
* **set** <_key_> <_value_> - Set a config value.
* **unset** <_key_> - Remove a config value.
* **add** <_key_> <_value_> - Add a value to an array.
* **del** <_key_> <_value_> - Remove a value from an array.
""")


def config_view(evt: CommandEvent, portal: po.Portal) -> Awaitable[EventID]:
    return evt.reply(f"Room-specific config:\n{_str_value(portal.local_config).rstrip()}")


def config_defaults(evt: CommandEvent) -> Awaitable[EventID]:
    value = _str_value({
        "bridge_notices": {
            "default": evt.config["bridge.bridge_notices.default"],
            "exceptions": evt.config["bridge.bridge_notices.exceptions"],
        },
        "bot_messages_as_notices": evt.config["bridge.bot_messages_as_notices"],
        "inline_images": evt.config["bridge.inline_images"],
        "message_formats": evt.config["bridge.message_formats"],
        "emote_format": evt.config["bridge.emote_format"],
        "state_event_formats": evt.config["bridge.state_event_formats"],
        "telegram_link_preview": evt.config["bridge.telegram_link_preview"],
    })
    return evt.reply(f"Bridge instance wide config:\n{value.rstrip()}")


def _str_value(value: Any) -> str:
    stream = StringIO()
    yaml.dump(value, stream)
    value_str = stream.getvalue()
    if "\n" in value_str:
        return f"\n```yaml\n{value_str}\n```\n"
    else:
        return f"`{value_str}`"


def config_set(evt: CommandEvent, portal: po.Portal, key: str, value: Any) -> Awaitable[EventID]:
    if not key or value is None:
        return evt.reply(f"**Usage:** `$cmdprefix+sp config set <key> <value>`")
    elif util.recursive_set(portal.local_config, key, value):
        return evt.reply(f"Successfully set the value of `{key}` to {_str_value(value)}".rstrip())
    else:
        return evt.reply(f"Failed to set value of `{key}`. "
                         "Does the path contain non-map types?")


def config_unset(evt: CommandEvent, portal: po.Portal, key: str) -> Awaitable[EventID]:
    if not key:
        return evt.reply(f"**Usage:** `$cmdprefix+sp config unset <key>`")
    elif util.recursive_del(portal.local_config, key):
        return evt.reply(f"Successfully deleted `{key}` from config.")
    else:
        return evt.reply(f"`{key}` not found in config.")


def config_add_del(evt: CommandEvent, portal: po.Portal, key: str, value: str, cmd: str
                   ) -> Awaitable[EventID]:
    if not key or value is None:
        return evt.reply(f"**Usage:** `$cmdprefix+sp config {cmd} <key> <value>`")

    arr = util.recursive_get(portal.local_config, key)
    if not arr:
        return evt.reply(f"`{key}` not found in config. "
                         f"Maybe do `$cmdprefix+sp config set {key} []` first?")
    elif not isinstance(arr, list):
        return evt.reply("`{key}` does not seem to be an array.")
    elif cmd == "add":
        if value in arr:
            return evt.reply(f"The array at `{key}` already contains {_str_value(value)}".rstrip())
        arr.append(value)
        return evt.reply(f"Successfully added {_str_value(value)} to the array at `{key}`")
    else:
        if value not in arr:
            return evt.reply(f"The array at `{key}` does not contain {_str_value(value)}")
        arr.remove(value)
        return evt.reply(f"Successfully removed {_str_value(value)} from the array at `{key}`")
