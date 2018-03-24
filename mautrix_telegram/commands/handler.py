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
import markdown
import logging

from telethon.errors import FloodWaitError

from ..util import format_duration

command_handlers = {}


def command_handler(needs_auth=True, management_only=False, needs_admin=False, name=None):
    def decorator(func):
        def wrapper(evt):
            if management_only and not evt.is_management:
                return evt.reply(f"`{evt.command}` is a restricted command:"
                                 "you may only run it in management rooms.")
            elif needs_auth and not evt.sender.logged_in:
                return evt.reply("This command requires you to be logged in.")
            elif needs_admin and not evt.sender.is_admin:
                return evt.reply("This is command requires administrator privileges.")
            return func(evt)

        command_handlers[name or func.__name__.replace("_", "-")] = wrapper
        return wrapper

    return decorator


class CommandEvent:
    def __init__(self, handler, room, sender, command, args, is_management, is_portal):
        self.az = handler.az
        self.log = handler.log
        self.loop = handler.loop
        self.tgbot = handler.tgbot
        self.config = handler.config
        self.command_prefix = handler.command_prefix
        self.room_id = room
        self.sender = sender
        self.command = command
        self.args = args
        self.is_management = is_management
        self.is_portal = is_portal

    def reply(self, message, allow_html=False, render_markdown=True):
        message = message.replace("$cmdprefix+sp ",
                                  "" if self.is_management else f"{self.command_prefix} ")
        message = message.replace("$cmdprefix", self.command_prefix)
        html = None
        if render_markdown:
            html = markdown.markdown(message, safe_mode="escape" if allow_html else False)
        elif allow_html:
            html = message
        return self.az.intent.send_notice(self.room_id, message, html=html)


class CommandHandler:
    log = logging.getLogger("mau.commands")

    def __init__(self, context):
        self.az, self.db, self.config, self.loop, self.tgbot = context
        self.command_prefix = self.config["bridge.command_prefix"]

    # region Utility functions for handling commands

    async def handle(self, room, sender, command, args, is_management, is_portal):
        evt = CommandEvent(self, room, sender, command, args,
                           is_management, is_portal)
        orig_command = command
        command = command.lower()
        try:
            command = command_handlers[command]
        except KeyError:
            if sender.command_status and "next" in sender.command_status:
                args.insert(0, orig_command)
                evt.command = ""
                command = sender.command_status["next"]
            else:
                command = command_handlers["unknown-command"]
        try:
            await command(evt)
        except FloodWaitError as e:
            return await evt.reply(f"Flood error: Please wait {format_duration(e.seconds)}")
        except Exception:
            self.log.exception("Fatal error handling command "
                               f"{evt.command} {' '.join(args)} from {sender.mxid}")
            return await evt.reply("Fatal error while handling command. "
                                   "Check logs for more details.")
