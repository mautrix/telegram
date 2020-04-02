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
"""This module contains classes handling commands issued by Matrix users."""
from typing import Awaitable, Callable, List, Optional, NamedTuple, Any

from telethon.errors import FloodWaitError

from mautrix.types import RoomID, EventID, MessageEventContent
from mautrix.bridge.commands import (HelpSection, CommandEvent as BaseCommandEvent,
                                     CommandHandler as BaseCommandHandler,
                                     CommandProcessor as BaseCommandProcessor,
                                     CommandHandlerFunc, command_handler as base_command_handler)

from ..util import format_duration
from .. import user as u, context as c

HelpCacheKey = NamedTuple('HelpCacheKey',
                          is_management=bool, is_portal=bool, puppet_whitelisted=bool,
                          matrix_puppet_whitelisted=bool, is_admin=bool, is_logged_in=bool)

SECTION_AUTH = HelpSection("Authentication", 10, "")
SECTION_CREATING_PORTALS = HelpSection("Creating portals", 20, "")
SECTION_PORTAL_MANAGEMENT = HelpSection("Portal management", 30, "")
SECTION_MISC = HelpSection("Miscellaneous", 40, "")
SECTION_ADMIN = HelpSection("Administration", 50, "")


class CommandEvent(BaseCommandEvent):
    sender: u.User

    def __init__(self, processor: 'CommandProcessor', room_id: RoomID, event_id: EventID,
                 sender: u.User, command: str, args: List[str], content: MessageEventContent,
                 is_management: bool, is_portal: bool) -> None:
        super().__init__(processor, room_id, event_id, sender, command, args, content,
                         is_management, is_portal)
        self.bridge = processor.bridge
        self.tgbot = processor.tgbot
        self.config = processor.config
        self.public_website = processor.public_website

    @property
    def print_error_traceback(self) -> bool:
        return self.sender.is_admin

    async def get_help_key(self) -> HelpCacheKey:
        return HelpCacheKey(self.is_management, self.is_portal, self.sender.puppet_whitelisted,
                            self.sender.matrix_puppet_whitelisted, self.sender.is_admin,
                            await self.sender.is_logged_in())


class CommandHandler(BaseCommandHandler):
    name: str

    management_only: bool
    needs_auth: bool
    needs_puppeting: bool
    needs_matrix_puppeting: bool
    needs_admin: bool

    def __init__(self, handler: Callable[[CommandEvent], Awaitable[EventID]],
                 management_only: bool, name: str, help_text: str, help_args: str,
                 help_section: HelpSection, needs_auth: bool, needs_puppeting: bool,
                 needs_matrix_puppeting: bool, needs_admin: bool) -> None:
        super().__init__(handler, management_only, name, help_text, help_args, help_section,
                         needs_auth=needs_auth, needs_puppeting=needs_puppeting,
                         needs_matrix_puppeting=needs_matrix_puppeting, needs_admin=needs_admin)

    async def get_permission_error(self, evt: CommandEvent) -> Optional[str]:
        if self.management_only and not evt.is_management:
            return (f"`{evt.command}` is a restricted command: "
                    "you may only run it in management rooms.")
        elif self.needs_puppeting and not evt.sender.puppet_whitelisted:
            return "This command requires puppeting privileges."
        elif self.needs_matrix_puppeting and not evt.sender.matrix_puppet_whitelisted:
            return "This command requires Matrix puppeting privileges."
        elif self.needs_admin and not evt.sender.is_admin:
            return "This command requires administrator privileges."
        elif self.needs_auth and not await evt.sender.is_logged_in():
            return "This command requires you to be logged in."
        return None

    def has_permission(self, key: HelpCacheKey) -> bool:
        return ((not self.management_only or key.is_management) and
                (not self.needs_puppeting or key.puppet_whitelisted) and
                (not self.needs_matrix_puppeting or key.matrix_puppet_whitelisted) and
                (not self.needs_admin or key.is_admin) and
                (not self.needs_auth or key.is_logged_in))


def command_handler(_func: Optional[CommandHandlerFunc] = None, *, needs_auth: bool = True,
                    needs_puppeting: bool = True, needs_matrix_puppeting: bool = False,
                    needs_admin: bool = False, management_only: bool = False,
                    name: Optional[str] = None, help_text: str = "", help_args: str = "",
                    help_section: HelpSection = None) -> Callable[[CommandHandlerFunc],
                                                                  CommandHandler]:
    return base_command_handler(
        _func, _handler_class=CommandHandler, name=name, help_text=help_text, help_args=help_args,
        help_section=help_section, management_only=management_only, needs_auth=needs_auth,
        needs_admin=needs_admin, needs_puppeting=needs_puppeting,
        needs_matrix_puppeting=needs_matrix_puppeting)


class CommandProcessor(BaseCommandProcessor):
    def __init__(self, context: c.Context) -> None:
        super().__init__(az=context.az, config=context.config, event_class=CommandEvent,
                         loop=context.loop, bridge=context.bridge)
        self.tgbot = context.bot
        self.bridge = context.bridge
        self.az, self.config, self.loop, self.tgbot = context.core
        self.public_website = context.public_website
        self.command_prefix = self.config["bridge.command_prefix"]

    @staticmethod
    async def _run_handler(handler: Callable[[CommandEvent], Awaitable[Any]], evt: CommandEvent
                           ) -> Any:
        try:
            return await handler(evt)
        except FloodWaitError as e:
            return await evt.reply(f"Flood error: Please wait {format_duration(e.seconds)}")
