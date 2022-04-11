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
from mautrix.bridge import InvalidAccessToken, OnlyLoginSelf
from mautrix.types import EventID

from .. import puppet as pu
from . import SECTION_AUTH, CommandEvent, command_handler


@command_handler(
    needs_auth=True,
    management_only=True,
    needs_matrix_puppeting=True,
    help_section=SECTION_AUTH,
    help_text="Replace your Telegram account's Matrix puppet with your own Matrix account.",
)
async def login_matrix(evt: CommandEvent) -> EventID:
    puppet = await pu.Puppet.get_by_tgid(evt.sender.tgid)
    if puppet.is_real_user:
        return await evt.reply(
            "You have already logged in with your Matrix account. "
            "Log out with `$cmdprefix+sp logout-matrix` first."
        )
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
                "your access token in the message history."
            )
        return await evt.reply(
            "This bridge instance does not allow logging in inside Matrix.\n\n"
            f"Please visit [the login page]({url}) to log in."
        )
    elif allow_matrix_login:
        return await evt.reply(
            "This bridge instance does not allow you to log in outside of Matrix.\n\n"
            "Please send your Matrix access token here to log in."
        )
    return await evt.reply("This bridge instance has been configured to not allow logging in.")


async def enter_matrix_token(evt: CommandEvent) -> EventID:
    evt.sender.command_status = None

    puppet = await pu.Puppet.get_by_tgid(evt.sender.tgid)
    if puppet.is_real_user:
        return await evt.reply(
            "You have already logged in with your Matrix account. "
            "Log out with `$cmdprefix+sp logout-matrix` first."
        )
    try:
        await puppet.switch_mxid(" ".join(evt.args), evt.sender.mxid)
    except OnlyLoginSelf:
        return await evt.reply("You can only log in as your own Matrix user.")
    except InvalidAccessToken:
        return await evt.reply("Failed to verify access token.")
    return await evt.reply(
        f"Replaced your Telegram account's Matrix puppet with {puppet.custom_mxid}."
    )
