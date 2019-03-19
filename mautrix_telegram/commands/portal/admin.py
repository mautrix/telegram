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
from typing import Dict
import asyncio

from mautrix_appservice import MatrixRequestError

from ... import portal as po, puppet as pu, user as u
from .. import command_handler, CommandEvent, SECTION_ADMIN


@command_handler(needs_admin=True, needs_auth=False, name="set-pl",
                 help_section=SECTION_ADMIN,
                 help_args="<_level_> [_mxid_]",
                 help_text="Set a temporary power level without affecting Telegram.")
async def set_power_level(evt: CommandEvent) -> Dict:
    try:
        level = int(evt.args[0])
    except KeyError:
        return await evt.reply("**Usage:** `$cmdprefix+sp set-pl <level> [mxid]`")
    except ValueError:
        return await evt.reply("The level must be an integer.")
    levels = await evt.az.intent.get_power_levels(evt.room_id)
    mxid = evt.args[1] if len(evt.args) > 1 else evt.sender.mxid
    levels["users"][mxid] = level
    try:
        await evt.az.intent.set_power_levels(evt.room_id, levels)
    except MatrixRequestError:
        evt.log.exception("Failed to set power level.")
        return await evt.reply("Failed to set power level.")
    return {}


@command_handler(needs_admin=True, needs_auth=False,
                 help_section=SECTION_ADMIN,
                 help_args="<`portal`|`puppet`|`user`>",
                 help_text="Clear internal bridge caches")
async def clear_db_cache(evt: CommandEvent) -> Dict:
    try:
        section = evt.args[0].lower()
    except IndexError:
        return await evt.reply("**Usage:** `$cmdprefix+sp clear-db-cache <section>`")
    if section == "portal":
        po.Portal.by_tgid = {}
        po.Portal.by_mxid = {}
        await evt.reply("Cleared portal cache")
    elif section == "puppet":
        pu.Puppet.cache = {}
        for puppet in pu.Puppet.by_custom_mxid.values():
            puppet.sync_task.cancel()
        pu.Puppet.by_custom_mxid = {}
        await asyncio.gather(
            *[puppet.init_custom_mxid() for puppet in pu.Puppet.all_with_custom_mxid()],
            loop=evt.loop)
        await evt.reply("Cleared puppet cache and restarted custom puppet syncers")
    elif section == "user":
        u.User.by_mxid = {
            user.mxid: user
            for user in u.User.by_tgid.values()
        }
        await evt.reply("Cleared non-logged-in user cache")
    else:
        return await evt.reply("**Usage:** `$cmdprefix+sp clear-db-cache <section>`")


@command_handler(needs_admin=True, needs_auth=False,
                 help_section=SECTION_ADMIN,
                 help_args="[_mxid_]",
                 help_text="Reload and reconnect a user")
async def reload_user(evt: CommandEvent) -> Dict:
    if len(evt.args) > 0:
        mxid = evt.args[0]
    else:
        mxid = evt.sender.mxid
    user = u.User.get_by_mxid(mxid, create=False)
    if not user:
        return await evt.reply("User not found")
    puppet = pu.Puppet.get_by_custom_mxid(mxid)
    if puppet:
        puppet.sync_task.cancel()
    await user.stop()
    user.delete(delete_db=False)
    user = u.User.get_by_mxid(mxid)
    await user.ensure_started()
    if puppet:
        await puppet.init_custom_mxid()
    await evt.reply(f"Reloaded and reconnected {user.mxid} (telegram: {user.human_tg_id})")
