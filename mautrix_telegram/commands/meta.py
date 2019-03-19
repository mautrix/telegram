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
from typing import Dict, List, Optional, Tuple

from . import command_handler, CommandEvent, _command_handlers, SECTION_GENERAL
from .handler import HelpSection


@command_handler(needs_auth=False, needs_puppeting=False,
                 help_section=SECTION_GENERAL,
                 help_text="Cancel an ongoing action (such as login)")
async def cancel(evt: CommandEvent) -> Optional[Dict]:
    if evt.sender.command_status:
        action = evt.sender.command_status["action"]
        evt.sender.command_status = None
        return await evt.reply(f"{action} cancelled.")
    else:
        return await evt.reply("No ongoing command.")


@command_handler(needs_auth=False, needs_puppeting=False)
async def unknown_command(evt: CommandEvent) -> Optional[Dict]:
    return await evt.reply("Unknown command. Try `$cmdprefix+sp help` for help.")


help_cache = {}  # type: Dict[Tuple[bool, bool, bool, bool, bool], str]


async def _get_help_text(evt: CommandEvent) -> str:
    cache_key = (evt.is_management, evt.sender.puppet_whitelisted,
                 evt.sender.matrix_puppet_whitelisted, evt.sender.is_admin,
                 await evt.sender.is_logged_in())
    if cache_key not in help_cache:
        help_sections = {}  # type: Dict[HelpSection, List[str]]
        for handler in _command_handlers.values():
            if handler.has_help and handler.has_permission(*cache_key):
                help_sections.setdefault(handler.help_section, [])
                help_sections[handler.help_section].append(handler.help + "  ")
        help_sorted = sorted(help_sections.items(), key=lambda item: item[0].order)
        helps = ["#### {}\n{}\n".format(key.name, "\n".join(value)) for key, value in help_sorted]
        help_cache[cache_key] = "\n".join(helps)
    return help_cache[cache_key]


def _get_management_status(evt: CommandEvent) -> str:
    if evt.is_management:
        return "This is a management room: prefixing commands with `$cmdprefix` is not required."
    elif evt.is_portal:
        return ("**This is a portal room**: you must always prefix commands with `$cmdprefix`.\n"
                "Management commands will not be sent to Telegram.")
    return "**This is not a management room**: you must prefix commands with `$cmdprefix`."


@command_handler(name="help", needs_auth=False, needs_puppeting=False,
                 help_section=SECTION_GENERAL,
                 help_text="Show this help message.")
async def help_cmd(evt: CommandEvent) -> Optional[Dict]:
    return await evt.reply(_get_management_status(evt) + "\n" + await _get_help_text(evt))
