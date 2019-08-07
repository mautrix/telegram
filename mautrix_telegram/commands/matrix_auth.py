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
from mautrix.types import EventID
from mautrix.bridge import InvalidAccessToken, OnlyLoginSelf

from . import command_handler, CommandEvent, SECTION_AUTH
from .. import puppet as pu


@command_handler(needs_auth=True, needs_matrix_puppeting=True,
                 help_section=SECTION_AUTH, help_text="Revert your Telegram account's Matrix "
                                                      "puppet to use the default Matrix account.")
async def logout_matrix(evt: CommandEvent) -> EventID:
    puppet = pu.Puppet.get(evt.sender.tgid)
    if not puppet.is_real_user:
        return await evt.reply("You are not logged in with your Matrix account.")
    await puppet.switch_mxid(None, None)
    return await evt.reply("Reverted your Telegram account's Matrix puppet back to the default.")


@command_handler(needs_auth=True, management_only=True, needs_matrix_puppeting=True,
                 help_section=SECTION_AUTH,
                 help_text="Replace your Telegram account's Matrix puppet with your own Matrix "
                           "account.")
async def login_matrix(evt: CommandEvent) -> EventID:
    puppet = pu.Puppet.get(evt.sender.tgid)
    if puppet.is_real_user:
        return await evt.reply("You have already logged in with your Matrix account. "
                               "Log out with `$cmdprefix+sp logout-matrix` first.")
    allow_matrix_login = evt.config.get("bridge.allow_matrix_login", True)
    if allow_matrix_login:
        evt.sender.command_status = {
            "next": enter_matrix_token,
            "action": "Matrix login",
        }
    if evt.config["appservice.public.enabled"]:
        prefix = evt.config["appservice.public.external"]
        token = evt.public_website.make_token(evt.sender.mxid, "/matrix-login")
        url = f"{prefix}/matrix-login?token={token}"
        if allow_matrix_login:
            return await evt.reply(
                "This bridge instance allows you to log in inside or outside Matrix.\n\n"
                "If you would like to log in within Matrix, please send your Matrix access token "
                "here.\n"
                f"If you would like to log in outside of Matrix, [click here]({url}).\n\n"
                "Logging in outside of Matrix is recommended, because in-Matrix login would save "
                "your access token in the message history.")
        return await evt.reply("This bridge instance does not allow logging in inside Matrix.\n\n"
                               f"Please visit [the login page]({url}) to log in.")
    elif allow_matrix_login:
        return await evt.reply(
            "This bridge instance does not allow you to log in outside of Matrix.\n\n"
            "Please send your Matrix access token here to log in.")
    return await evt.reply("This bridge instance has been configured to not allow logging in.")


@command_handler(needs_auth=True, needs_matrix_puppeting=True,
                 help_section=SECTION_AUTH,
                 help_text="Pings the server with the stored matrix authentication.")
async def ping_matrix(evt: CommandEvent) -> EventID:
    puppet = pu.Puppet.get(evt.sender.tgid)
    if not puppet.is_real_user:
        return await evt.reply("You are not logged in with your Matrix account.")
    try:
        await puppet.start()
    except InvalidAccessToken:
        return await evt.reply("Your access token is invalid.")
    return await evt.reply("Your Matrix login is working.")


@command_handler(needs_auth=True, needs_matrix_puppeting=True, help_section=SECTION_AUTH,
                 help_text="Clear the Matrix sync token stored for your custom puppet.")
async def clear_cache_matrix(evt: CommandEvent) -> EventID:
    puppet = pu.Puppet.get(evt.sender.tgid)
    if not puppet.is_real_user:
        return await evt.reply("You are not logged in with your Matrix account.")
    try:
        puppet.stop()
        puppet.next_batch = None
        await puppet.start()
    except InvalidAccessToken:
        return await evt.reply("Your access token is invalid.")
    return await evt.reply("Cleared cache successfully.")


async def enter_matrix_token(evt: CommandEvent) -> EventID:
    evt.sender.command_status = None

    puppet = pu.Puppet.get(evt.sender.tgid)
    if puppet.is_real_user:
        return await evt.reply("You have already logged in with your Matrix account. "
                               "Log out with `$cmdprefix+sp logout-matrix` first.")
    try:
        await puppet.switch_mxid(" ".join(evt.args), evt.sender.mxid)
    except OnlyLoginSelf:
        return await evt.reply("You can only log in as your own Matrix user.")
    except InvalidAccessToken:
        return await evt.reply("Failed to verify access token.")
    return await evt.reply("Replaced your Telegram account's Matrix puppet "
                           f"with {puppet.custom_mxid}.")
