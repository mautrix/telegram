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
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable, NamedTuple

from telethon.errors import FloodWaitError

from mautrix.bridge.commands import (
    CommandEvent as BaseCommandEvent,
    CommandHandler as BaseCommandHandler,
    CommandHandlerFunc,
    CommandProcessor as BaseCommandProcessor,
    HelpSection,
    command_handler as base_command_handler,
)
from mautrix.types import EventID, MessageEventContent, RoomID
from mautrix.util.format_duration import format_duration

from .. import portal as po, user as u

if TYPE_CHECKING:
    from ..__main__ import TelegramBridge


class HelpCacheKey(NamedTuple):
    is_management: bool
    is_portal: bool
    puppet_whitelisted: bool
    matrix_puppet_whitelisted: bool
    is_admin: bool
    is_logged_in: bool


SECTION_AUTH = HelpSection("Authentication", 10, "")
SECTION_CREATING_PORTALS = HelpSection("Creating portals", 20, "")
SECTION_PORTAL_MANAGEMENT = HelpSection("Portal management", 30, "")
SECTION_MISC = HelpSection("Miscellaneous", 40, "")
SECTION_ADMIN = HelpSection("Administration", 50, "")


class CommandEvent(BaseCommandEvent):
    sender: u.User
    portal: po.Portal

    def __init__(
        self,
        processor: CommandProcessor,
        room_id: RoomID,
        event_id: EventID,
        sender: u.User,
        command: str,
        args: list[str],
        content: MessageEventContent,
        portal: po.Portal | None,
        is_management: bool,
        has_bridge_bot: bool,
    ) -> None:
        super().__init__(
            processor,
            room_id,
            event_id,
            sender,
            command,
            args,
            content,
            portal,
            is_management,
            has_bridge_bot,
        )
        self.bridge = processor.bridge
        self.tgbot = processor.tgbot
        self.config = processor.config
        self.public_website = processor.public_website

    @property
    def print_error_traceback(self) -> bool:
        return self.sender.is_admin

    async def get_help_key(self) -> HelpCacheKey:
        return HelpCacheKey(
            self.is_management,
            self.portal is not None,
            self.sender.puppet_whitelisted,
            self.sender.matrix_puppet_whitelisted,
            self.sender.is_admin,
            await self.sender.is_logged_in(),
        )


class CommandHandler(BaseCommandHandler):
    name: str

    needs_puppeting: bool
    needs_matrix_puppeting: bool

    def __init__(
        self,
        handler: Callable[[CommandEvent], Awaitable[EventID]],
        management_only: bool,
        name: str,
        help_text: str,
        help_args: str,
        help_section: HelpSection,
        needs_auth: bool,
        needs_puppeting: bool,
        needs_matrix_puppeting: bool,
        needs_admin: bool,
        **kwargs,
    ) -> None:
        super().__init__(
            handler,
            management_only,
            name,
            help_text,
            help_args,
            help_section,
            needs_auth=needs_auth,
            needs_puppeting=needs_puppeting,
            needs_matrix_puppeting=needs_matrix_puppeting,
            needs_admin=needs_admin,
            **kwargs,
        )

    async def get_permission_error(self, evt: CommandEvent) -> str | None:
        if self.needs_puppeting and not evt.sender.puppet_whitelisted:
            return "That command is limited to users with puppeting privileges."
        elif self.needs_matrix_puppeting and not evt.sender.matrix_puppet_whitelisted:
            return "That command is limited to users with full puppeting privileges."
        return await super().get_permission_error(evt)

    def has_permission(self, key: HelpCacheKey) -> bool:
        return (
            super().has_permission(key)
            and (not self.needs_puppeting or key.puppet_whitelisted)
            and (not self.needs_matrix_puppeting or key.matrix_puppet_whitelisted)
        )


def command_handler(
    _func: CommandHandlerFunc | None = None,
    *,
    needs_auth: bool = True,
    needs_puppeting: bool = True,
    needs_matrix_puppeting: bool = False,
    needs_admin: bool = False,
    management_only: bool = False,
    name: str | None = None,
    help_text: str = "",
    help_args: str = "",
    help_section: HelpSection = None,
) -> Callable[[CommandHandlerFunc], CommandHandler]:
    return base_command_handler(
        _func,
        _handler_class=CommandHandler,
        name=name,
        help_text=help_text,
        help_args=help_args,
        help_section=help_section,
        management_only=management_only,
        needs_auth=needs_auth,
        needs_admin=needs_admin,
        needs_puppeting=needs_puppeting,
        needs_matrix_puppeting=needs_matrix_puppeting,
    )


class CommandProcessor(BaseCommandProcessor):
    def __init__(self, bridge: "TelegramBridge") -> None:
        super().__init__(event_class=CommandEvent, bridge=bridge)
        self.tgbot = bridge.bot
        self.public_website = bridge.public_website

    @staticmethod
    async def _run_handler(
        handler: Callable[[CommandEvent], Awaitable[Any]], evt: CommandEvent
    ) -> Any:
        try:
            return await handler(evt)
        except FloodWaitError as e:
            return await evt.reply(f"Flood error: Please wait {format_duration(e.seconds)}")
