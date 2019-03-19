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
from typing import Dict, Optional

from ... import portal as po
from .. import command_handler, CommandEvent, SECTION_ADMIN


@command_handler(needs_admin=True,
                 help_section=SECTION_ADMIN,
                 help_args="<`whitelist`|`blacklist`>",
                 help_text="Change whether the bridge will allow or disallow bridging rooms by "
                           "default.")
async def filter_mode(evt: CommandEvent) -> Dict:
    try:
        mode = evt.args[0]
        if mode not in ("whitelist", "blacklist"):
            raise ValueError()
    except (IndexError, ValueError):
        return await evt.reply("**Usage:** `$cmdprefix+sp filter-mode <whitelist/blacklist>`")

    evt.config["bridge.filter.mode"] = mode
    evt.config.save()
    po.Portal.filter_mode = mode
    if mode == "whitelist":
        return await evt.reply("The bridge will now disallow bridging chats by default.\n"
                               "To allow bridging a specific chat, use"
                               "`!filter whitelist <chat ID>`.")
    else:
        return await evt.reply("The bridge will now allow bridging chats by default.\n"
                               "To disallow bridging a specific chat, use"
                               "`!filter blacklist <chat ID>`.")


@command_handler(name="filter", needs_admin=True,
                 help_section=SECTION_ADMIN,
                 help_args="<`whitelist`|`blacklist`> <_chat ID_>",
                 help_text="Allow or disallow bridging a specific chat.")
async def edit_filter(evt: CommandEvent) -> Optional[Dict]:
    try:
        action = evt.args[0]
        if action not in ("whitelist", "blacklist", "add", "remove"):
            raise ValueError()

        id_str = evt.args[1]
        if id_str.startswith("-100"):
            filter_id = int(id_str[4:])
        elif id_str.startswith("-"):
            filter_id = int(id_str[1:])
        else:
            filter_id = int(id_str)
    except (IndexError, ValueError):
        return await evt.reply("**Usage:** `$cmdprefix+sp filter <whitelist/blacklist> <chat ID>`")

    mode = evt.config["bridge.filter.mode"]
    if mode not in ("blacklist", "whitelist"):
        return await evt.reply(f"Unknown filter mode \"{mode}\". Please fix the bridge config.")

    filter_id_list = evt.config["bridge.filter.list"]

    if action in ("blacklist", "whitelist"):
        action = "add" if mode == action else "remove"

    def save() -> None:
        evt.config["bridge.filter.list"] = filter_id_list
        evt.config.save()
        po.Portal.filter_list = filter_id_list

    if action == "add":
        if filter_id in filter_id_list:
            return await evt.reply(f"That chat is already {mode}ed.")
        filter_id_list.append(filter_id)
        save()
        return await evt.reply(f"Chat ID added to {mode}.")
    elif action == "remove":
        if filter_id not in filter_id_list:
            return await evt.reply(f"That chat is not {mode}ed.")
        filter_id_list.remove(filter_id)
        save()
        return await evt.reply(f"Chat ID removed from {mode}.")
    return None
