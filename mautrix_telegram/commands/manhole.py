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
from typing import Optional, Callable
import asyncio
import sys
import os

from telethon import __version__ as __telethon_version__

from mautrix import __version__ as __mautrix_version__
from mautrix.types import UserID
from mautrix.util.manhole import start_manhole

from .. import __version__
from . import command_handler, CommandEvent, SECTION_ADMIN


class State:
    manhole: Optional[asyncio.AbstractServer] = None
    opened_by: Optional[UserID] = None
    close: Optional[Callable[[], None]] = None


@command_handler(needs_auth=False, needs_admin=True, help_section=SECTION_ADMIN,
                 help_text="Open a manhole into the bridge.")
async def manhole(evt: CommandEvent) -> None:
    if not evt.config["manhole.enabled"]:
        await evt.reply("The manhole has been disabled in the config.")
        return

    if State.manhole:
        await evt.reply(f"There's an existing manhole opened by {State.opened_by}")
        return

    from ..portal import Portal
    from ..puppet import Puppet
    from ..user import User
    namespace = {
        "bridge": evt.bridge,
        "User": User,
        "Portal": Portal,
        "Puppet": Puppet,
    }
    banner = (f"Python {sys.version} on {sys.platform}\n"
              f"mautrix-telegram {__version__} with mautrix-python {__mautrix_version__} "
              f"and Telethon {__telethon_version__}\n\nManhole opened by {evt.sender.mxid}\n")
    path = evt.config["manhole.path"]

    evt.log.info(f"{evt.sender.mxid} opened a manhole.")
    State.manhole, State.close = await start_manhole(path=path, banner=banner, namespace=namespace,
                                                     loop=evt.loop)
    State.opened_by = evt.sender.mxid
    await evt.reply(f"Opened manhole at unix://{path}")
    await State.manhole.wait_closed()
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    evt.log.info(f"{evt.sender.mxid}'s manhole was closed.")
    await evt.reply("Your manhole was closed.")


@command_handler(needs_auth=False, needs_admin=True, help_section=SECTION_ADMIN,
                 help_text="Close an open manhole.")
async def close_manhole(evt: CommandEvent) -> None:
    if not State.manhole:
        await evt.reply("There is no open manhole.")
        return

    opened_by = State.opened_by
    State.close()
    State.manhole = None
    State.close = None
    State.opened_by = None
    if opened_by != evt.sender:
        await evt.reply(f"Closed manhole opened by {opened_by}")
