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
"""This module contains classes handling commands issued by Matrix users."""
from typing import Awaitable, Callable, Dict, List, NamedTuple, Optional
import logging
import traceback

import commonmark

from telethon.errors import FloodWaitError

from ..types import MatrixRoomID, MatrixEventID
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


def ensure_trailing_newline(s: str) -> str:
    """Returns the passed string, but with a guaranteed trailing newline."""
    return s + ("" if s[-1] == "\n" else "\n")


class CommandEvent:
    """Holds information about a command issued in a Matrix room.

    When a Matrix command was issued to the bot, CommandEvent will hold
    information regarding the event.

    Attributes:
        room_id: The id of the Matrix room in which the command was issued.
        event_id: The id of the matrix event which contained the command.
        sender: The user who issued the command.
        command: The issued command.
        args: Arguments given with the issued command.
        is_management: Determines whether the room in which the command wa
            issued is a management room.
        is_portal: Determines whether the room in which the command was issued
            is a portal.
    """

    def __init__(self, processor: 'CommandProcessor', room: MatrixRoomID, event: MatrixEventID,
                 sender: u.User, command: str, args: List[str], is_management: bool,
                 is_portal: bool) -> None:
        self.az = processor.az
        self.log = processor.log
        self.loop = processor.loop
        self.tgbot = processor.tgbot
        self.config = processor.config
        self.public_website = processor.public_website
        self.command_prefix = processor.command_prefix
        self.room_id = room
        self.event_id = event
        self.sender = sender
        self.command = command
        self.args = args
        self.is_management = is_management
        self.is_portal = is_portal

    def reply(self, message: str, allow_html: bool = False, render_markdown: bool = True
              ) -> Awaitable[Dict]:
        """Write a reply to the room in which the command was issued.

        Replaces occurences of "$cmdprefix" in the message with the command
        prefix and replaces occurences of "$cmdprefix+sp " with the command
        prefix if the command was not issued in a management room.
        If allow_html and render_markdown are both False, the message will not
        be rendered to html and sending of html is disabled.

        Args:
            message: The message to post in the room.
            allow_html: Escape html in the message or don't render html at all
                if markdown is disabled.
            render_markdown: Use markdown formatting to render the passed
                message to html.

        Returns:
            Handler for the message sending function.
        """
        message_cmd = self._replace_command_prefix(message)
        html = self._render_message(message_cmd, allow_html=allow_html,
                                    render_markdown=render_markdown)

        return self.az.intent.send_notice(self.room_id, message_cmd, html=html)

    def mark_read(self) -> Awaitable[Dict]:
        """Marks the command as read by the bot."""
        return self.az.intent.mark_read(self.room_id, self.event_id)

    def _replace_command_prefix(self, message: str) -> str:
        """Returns the string with the proper command prefix entered."""
        message = message.replace(
            "$cmdprefix+sp ", "" if self.is_management else f"{self.command_prefix} "
        )
        return message.replace("$cmdprefix", self.command_prefix)

    @staticmethod
    def _render_message(message: str, allow_html: bool, render_markdown: bool) -> Optional[str]:
        """Renders the message as HTML.

        Args:
            allow_html: Flag to allow custom HTML in the message.
            render_markdown: If true, markdown styling is applied to the message.

        Returns:
            The message rendered as HTML.
            None is returned if no styled output is required.
        """
        html = ""
        if render_markdown:
            md_renderer.allow_html = allow_html
            html = md_renderer.render(md_parser.parse(message))
        elif allow_html:
            html = message
        return ensure_trailing_newline(html) if html else None


class CommandHandler:
    """A command which can be executed from a Matrix room.

    The command manages its permission and help texts.
    When called, it will check the permission of the command event and execute
    the command or, in case of error, report back to the user.

    Attributes:
        needs_auth: Flag indicating if the sender is required to be logged in.
        needs_puppeting: Flag indicating if the sender is required to use
            Telegram puppeteering for this command.
        needs_matrix_puppeting: Flag indicating if the sender is required to use
            Matrix pupeteering.
        needs_admin: Flag for whether only admin users can issue this command.
        management_only: Whether the command can exclusively be issued in a
            management room.
        name: The name of this command.
        help_section: Section of the help in which this command will appear.
    """

    def __init__(self, handler: Callable[[CommandEvent], Awaitable[Dict]], needs_auth: bool,
                 needs_puppeting: bool, needs_matrix_puppeting: bool, needs_admin: bool,
                 management_only: bool, name: str, help_text: str, help_args: str,
                 help_section: HelpSection) -> None:
        """
        Args:
            handler: The function handling the execution of this command.
            needs_auth: Flag indicating if the sender is required to be logged in.
            needs_puppeting: Flag indicating if the sender is required to use
                Telegram puppeteering for this command.
            needs_matrix_puppeting: Flag indicating if the sender is required to
                use Matrix pupeteering.
            needs_admin: Flag for whether only admin users can issue this command.
            management_only: Whether the command can exclusively be issued
                in a management room.
            name: The name of this command.
            help_text: The text displayed in the help for this command.
            help_args: Help text for the arguments of this command.
            help_section: Section of the help in which this command will appear.
        """
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
        """Returns the reason why the command could not be issued.

        Args:
            evt: The event for which to get the error information.

        Returns:
            A string describing the error or None if there was no error.
        """
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
        """Checks the permission for this command with the given status.

        Args:
            is_management: If the room in which the command will be issued is a
                management room.
            puppet_whitelisted: If the connected Telegram account puppet is
                allowed to issue the command.
            matrix_puppet_whitelisted: If the connected Matrix account puppet is
                allowed to issue the command.
            is_admin: If the issuing user is an admin.
            is_logged_in: If the issuing user is logged in.

        Returns:
            True if a user with the given state is allowed to issue the
            command.
        """
        return ((not self.management_only or is_management) and
                (not self.needs_puppeting or puppet_whitelisted) and
                (not self.needs_matrix_puppeting or matrix_puppet_whitelisted) and
                (not self.needs_admin or is_admin) and
                (not self.needs_auth or is_logged_in))

    async def __call__(self, evt: CommandEvent) -> Dict:
        """Executes the command if evt was issued with proper rights.

        Args:
            evt: The CommandEvent for which to check permissions.

        Returns:
            The result of the command or the error message function.

        Raises:
            FloodWaitError
        """
        error = await self.get_permission_error(evt)
        if error is not None:
            return await evt.reply(error)
        return await self._handler(evt)

    @property
    def has_help(self) -> bool:
        """Returns true if this command has a help text."""
        return bool(self.help_section) and bool(self._help_text)

    @property
    def help(self) -> str:
        """Returns the help text to this command."""
        return f"**{self.name}** {self._help_args} - {self._help_text}"


def command_handler(_func: Optional[Callable[[CommandEvent], Awaitable[Dict]]] = None, *,
                    needs_auth: bool = True, needs_puppeting: bool = True,
                    needs_matrix_puppeting: bool = False, needs_admin: bool = False,
                    management_only: bool = False, name: Optional[str] = None,
                    help_text: str = "", help_args: str = "", help_section: HelpSection = None
                    ) -> Callable[[Callable[[CommandEvent], Awaitable[Optional[Dict]]]],
                                  CommandHandler]:
    def decorator(func: Callable[[CommandEvent], Awaitable[Optional[Dict]]]) -> CommandHandler:
        actual_name = name or func.__name__.replace("_", "-")
        handler = CommandHandler(func, needs_auth, needs_puppeting, needs_matrix_puppeting,
                                 needs_admin, management_only, actual_name, help_text, help_args,
                                 help_section)
        command_handlers[handler.name] = handler
        return handler

    return decorator if _func is None else decorator(_func)


class CommandProcessor:
    """Handles the raw commands issued by a user to the Matrix bot."""
    log = logging.getLogger("mau.commands")

    def __init__(self, context: c.Context) -> None:
        self.az, self.config, self.loop, self.tgbot = context.core
        self.public_website = context.public_website
        self.command_prefix = self.config["bridge.command_prefix"]

    async def handle(self, room: MatrixRoomID, event_id: MatrixEventID, sender: u.User,
                     command: str, args: List[str], is_management: bool, is_portal: bool
                     ) -> Optional[Dict]:
        """Handles the raw commands issued by a user to the Matrix bot.

        If the command is not known, it might be a followup command and is
        delegated to a command handler registered for that purpose in the
        senders command_status as "next".

        Args:
            room: ID of the Matrix room in which the command was issued.
            event_id: ID of the event by which the command was issued.
            sender: The sender who issued the command.
            command: The issued command, case insensitive.
            args: Arguments given with the command.
            is_management: Whether the room is a management room.
            is_portal: Whether the room is a portal.

        Returns:
            The result of the error message function or None if no error
            occured. Unknown and delegated commands do not count as errors.
        """
        if not command_handlers or "unknown-command" not in command_handlers:
            raise ValueError("command_handlers are not properly initialized.")

        evt = CommandEvent(self, room, event_id, sender, command, args, is_management, is_portal)
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
            if evt.sender.is_admin and evt.is_management:
                return await evt.reply("Unhandled error while handling command:\n\n"
                                       "```traceback\n"
                                       f"{traceback.format_exc()}"
                                       "```")
            return await evt.reply("Unhandled error while handling command. "
                                   "Check logs for more details.")
        return None
