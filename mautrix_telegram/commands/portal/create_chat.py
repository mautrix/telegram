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

from mautrix.types import EventID

from ... import portal as po
from ...types import TelegramID
from .. import SECTION_CREATING_PORTALS, CommandEvent, command_handler
from .util import get_initial_state, user_has_power_level, warn_missing_power


@command_handler(
    help_section=SECTION_CREATING_PORTALS,
    help_args="[_type_]",
    help_text=(
        "Create a Telegram chat of the given type for the current Matrix room. "
        "The type is either `group`, `supergroup` or `channel` (defaults to `supergroup`)."
    ),
)
async def create(evt: CommandEvent) -> EventID:
    type = evt.args[0] if len(evt.args) > 0 else "supergroup"
    if type not in ("chat", "group", "supergroup", "channel"):
        return await evt.reply(
            "**Usage:** `$cmdprefix+sp create ['group'/'supergroup'/'channel']`"
        )

    if await po.Portal.get_by_mxid(evt.room_id):
        return await evt.reply("This is already a portal room.")

    if not await user_has_power_level(evt.room_id, evt.az.intent, evt.sender, "bridge"):
        return await evt.reply("You do not have the permissions to bridge this room.")

    title, about, levels, encrypted = await get_initial_state(evt.az.intent, evt.room_id)
    if not title:
        return await evt.reply("Please set a title before creating a Telegram chat.")

    supergroup = type == "supergroup"
    type = {
        "supergroup": "channel",
        "channel": "channel",
        "chat": "chat",
        "group": "chat",
    }[type]

    portal = po.Portal(
        tgid=TelegramID(0),
        tg_receiver=TelegramID(0),
        peer_type=type,
        mxid=evt.room_id,
        title=title,
        about=about,
        encrypted=encrypted,
    )
    invites, errors = await portal.get_telegram_users_in_matrix_room(evt.sender, pre_create=True)
    if len(errors) > 0:
        error_list = "\n".join(f"* [{mxid}](https://matrix.to/#/{mxid})" for mxid in errors)
        await evt.reply(
            f"Failed to add the following users to the chat:\n\n{error_list}\n\n"
            "You can try `$cmdprefix+sp search -r <username>` to help the bridge find "
            "those users."
        )

    await warn_missing_power(levels, evt)

    try:
        await portal.create_telegram_chat(evt.sender, invites=invites, supergroup=supergroup)
    except ValueError as e:
        await portal.delete()
        return await evt.reply(e.args[0])
