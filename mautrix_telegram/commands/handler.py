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
from typing import Awaitable, Callable, Dict, List, NamedTuple, Optional
import commonmark
import logging

from telethon.errors import FloodWaitError

from ..types import MatrixRoomID
from ..util import format_duration
from .. import user as u, context as c

command_handlers = {}  # type: Dict[str, CommandHandler]

HelpSection = NamedTuple('HelpSection', [('name', str), ('order', int), ('description', str)])

SECTION_GENERAL = HelpSection("General", 0, "")
SECTION_AUTH = HelpSection("Authentication", 10, "")
SECTION_CREATING_PORTALS = HelpSection("Creating portals", 20, "")
SECTION_PORTAL_MANAGEMENT = HelpSection("Portal management", 30, "")
SECTION_MISC = HelpSection("Miscellaneous", 40, "")
SECTION_ADMIN = HelpSection("Administration", 50, "")


class HtmlEscapingRenderer(commonmark.HtmlRenderer):
    def __init__(self, allow_html: bool = False):
        super().__init__()
        self.allow_html = allow_html

    def lit(self, s):
        if self.allow_html:
            return super().lit(s)
        return super().lit(s.replace("<", "&lt;").replace(">", "&gt;"))

    def image(self, node, entering):
        prev = self.allow_html
        self.allow_html = True
        super().image(node, entering)
        self.allow_html = prev


md_parser = commonmark.Parser()
md_renderer = HtmlEscapingRenderer()


class CommandEvent:
    def __init__(self, processor: 'CommandProcessor', room: MatrixRoomID, sender: u.User,
                 command: str, args: List[str], is_management: bool, is_portal: bool) -> None:
        self.az = processor.az
        self.log = processor.log
        self.loop = processor.loop
        self.tgbot = processor.tgbot
        self.config = processor.config
        self.public_website = processor.public_website
        self.command_prefix = processor.command_prefix
        self.room_id = room
        self.sender = sender
        self.command = command
        self.args = args
        self.is_management = is_management
        self.is_portal = is_portal

    def reply(self, message: str, allow_html: bool = False, render_markdown: bool = True
              ) -> Awaitable[Dict]:
        message = message.replace("$cmdprefix+sp ",
                                  "" if self.is_management else f"{self.command_prefix} ")
        message = message.replace("$cmdprefix", self.command_prefix)
        html = None
        if render_markdown:
            md_renderer.allow_html = allow_html
            html = md_renderer.render(md_parser.parse(message))
        elif allow_html:
            html = message
        return self.az.intent.send_notice(self.room_id, message, html=html)


class CommandHandler:
    def __init__(self, handler: Callable[[CommandEvent], Awaitable[Dict]], needs_auth: bool,
                 needs_puppeting: bool, needs_matrix_puppeting: bool, needs_admin: bool,
                 management_only: bool, name: str, help_text: str, help_args: str,
                 help_section: HelpSection) -> None:
        self._handler = handler
        self.needs_auth = needs_auth
        self.needs_puppeting = needs_puppeting
        self.needs_matrix_puppeting = needs_matrix_puppeting
        self.needs_admin = needs_admin
        self.management_only = management_only
        self.name = name
        self._help_text = help_text
        self._help_args = help_args
        self.help_section = help_section

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

    def has_permission(self, is_management: bool, puppet_whitelisted: bool,
                       matrix_puppet_whitelisted: bool, is_admin: bool, is_logged_in: bool) -> bool:
        return ((not self.management_only or is_management) and
                (not self.needs_puppeting or puppet_whitelisted) and
                (not self.needs_matrix_puppeting or matrix_puppet_whitelisted) and
                (not self.needs_admin or is_admin) and
                (not self.needs_auth or is_logged_in))

    async def __call__(self, evt: CommandEvent
                       ) -> Dict:
        error = await self.get_permission_error(evt)
        if error is not None:
            return await evt.reply(error)
        return await self._handler(evt)

    @property
    def has_help(self) -> bool:
        return bool(self.help_section) and bool(self._help_text)

    @property
    def help(self) -> str:
        return f"**{self.name}** {self._help_args} - {self._help_text}"


def command_handler(_func: Optional[Callable[[CommandEvent], Awaitable[Dict]]] = None, *,
                    needs_auth: bool = True,
                    needs_puppeting: bool = True,
                    needs_matrix_puppeting: bool = False,
                    needs_admin: bool = False,
                    management_only: bool = False,
                    name: Optional[str] = None,
                    help_text: str = "",
                    help_args: str = "",
                    help_section: HelpSection = None
                    ) -> Callable[[Callable[[CommandEvent], Awaitable[Optional[Dict]]]],
                                  CommandHandler]:
    input_name = name

    def decorator(func: Callable[[CommandEvent], Awaitable[Optional[Dict]]]) -> CommandHandler:
        name = input_name or func.__name__.replace("_", "-")
        handler = CommandHandler(func, needs_auth, needs_puppeting, needs_matrix_puppeting,
                                 needs_admin, management_only, name, help_text, help_args,
                                 help_section)
        command_handlers[handler.name] = handler
        return handler

    return decorator if _func is None else decorator(_func)


class CommandProcessor:
    log = logging.getLogger("mau.commands")

    def __init__(self, context: c.Context) -> None:
        self.az, self.db, self.config, self.loop, self.tgbot = context.core
        self.public_website = context.public_website
        self.command_prefix = self.config["bridge.command_prefix"]

    async def handle(self, room: MatrixRoomID, sender: u.User, command: str, args: List[str],
                     is_management: bool, is_portal: bool) -> Optional[Dict]:
        evt = CommandEvent(self, room, sender, command, args, is_management, is_portal)
        orig_command = command
        command = command.lower()
        try:
            handler = command_handlers[command]
        except KeyError:
            if sender.command_status and "next" in sender.command_status:
                args.insert(0, orig_command)
                evt.command = ""
                handler = sender.command_status["next"]
            else:
                handler = command_handlers["unknown-command"]
        try:
            await handler(evt)
        except FloodWaitError as e:
            return await evt.reply(f"Flood error: Please wait {format_duration(e.seconds)}")
        except Exception:
            self.log.exception("Unhandled error while handling command "
                               f"{evt.command} {' '.join(args)} from {sender.mxid}")
            return await evt.reply("Unhandled error while handling command. "
                                   "Check logs for more details.")
        return None
