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
import asyncio

from mautrix.types import EventID

from ... import portal as po, puppet as pu, user as u
from .. import SECTION_ADMIN, CommandEvent, command_handler


@command_handler(
    needs_admin=True,
    needs_auth=False,
    help_section=SECTION_ADMIN,
    help_args="<`portal`|`puppet`|`user`>",
    help_text="Clear internal bridge caches",
)
async def clear_db_cache(evt: CommandEvent) -> EventID:
    try:
        section = evt.args[0].lower()
    except IndexError:
        return await evt.reply("**Usage:** `$cmdprefix+sp clear-db-cache <section>`")
    if section == "portal":
        po.Portal.by_tgid = {}
        po.Portal.by_mxid = {}
        await evt.reply("Cleared portal cache")
    elif section == "puppet":
        pu.Puppet.by_tgid = {}
        for puppet in pu.Puppet.by_custom_mxid.values():
            puppet.stop()
        pu.Puppet.by_custom_mxid = {}
        await asyncio.gather(
            *[puppet.try_start() async for puppet in pu.Puppet.all_with_custom_mxid()]
        )
        await evt.reply("Cleared puppet cache and restarted custom puppet syncers")
    elif section == "user":
        u.User.by_mxid = {user.mxid: user for user in u.User.by_tgid.values()}
        await evt.reply("Cleared non-logged-in user cache")
    else:
        return await evt.reply("**Usage:** `$cmdprefix+sp clear-db-cache <section>`")


@command_handler(
    needs_admin=True,
    needs_auth=False,
    help_section=SECTION_ADMIN,
    help_args="[_mxid_]",
    help_text="Reload and reconnect a user",
)
async def reload_user(evt: CommandEvent) -> EventID:
    if len(evt.args) > 0:
        mxid = evt.args[0]
    else:
        mxid = evt.sender.mxid
    user = await u.User.get_by_mxid(mxid, create=False)
    if not user:
        return await evt.reply("User not found")
    puppet = await pu.Puppet.get_by_custom_mxid(mxid)
    if puppet:
        puppet.stop()
    await user.stop()
    del u.User.by_tgid[user.tgid]
    del u.User.by_mxid[user.mxid]
    user = await u.User.get_by_mxid(mxid)
    await user.ensure_started()
    if puppet:
        await puppet.start()
    return await evt.reply(f"Reloaded and reconnected {user.mxid} (telegram: {user.human_tg_id})")
