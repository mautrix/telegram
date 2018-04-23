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
import asyncio

from telethon.errors import *

from . import command_handler
from .. import puppet as pu
from ..util import format_duration


@command_handler(needs_auth=False)
async def ping(evt):
    if not evt.sender.logged_in:
        return await evt.reply("You're not logged in.")
    me = await evt.sender.client.get_me()
    if me:
        return await evt.reply(f"You're logged in as @{me.username}")
    else:
        return await evt.reply("You're not logged in.")


@command_handler()
async def ping_bot(evt):
    if not evt.tgbot:
        return await evt.reply("Telegram message relay bot not configured.")
    bot_info = await evt.tgbot.client.get_me()
    mxid = pu.Puppet.get_mxid_from_id(bot_info.id)
    displayname = bot_info.first_name
    return await evt.reply("Telegram message relay bot is active: "
                           f"[{displayname}](https://matrix.to/#/{mxid}) (ID {bot_info.id})\n\n"
                           "To use the bot, simply invite it to a portal room.")


@command_handler(needs_auth=False, management_only=True)
def register(evt):
    return evt.reply("Not yet implemented.")


@command_handler(needs_auth=False, management_only=True)
async def register(evt):
    if evt.sender.logged_in:
        return await evt.reply("You are already logged in.")
    elif len(evt.args) < 1:
        return await evt.reply("**Usage:** `$cmdprefix+sp register <phone> <full name>`")

    phone_number = evt.args[0]
    if len(evt.args) == 2:
        full_name = evt.args[1], ""
    else:
        full_name = " ".join(evt.args[1:-1]), evt.args[-1]

    await request_code(evt, phone_number, {
        "next": enter_code_register,
        "action": "Register",
        "full_name": full_name,
    })


async def enter_code_register(evt):
    if len(evt.args) == 0:
        return await evt.reply("**Usage:** `$cmdprefix+sp <code>`")
    try:
        await evt.sender.ensure_started(even_if_no_session=True)
        first_name, last_name = evt.sender.command_status["full_name"]
        user = await evt.sender.client.sign_up(evt.args[0], first_name, last_name)
        asyncio.ensure_future(evt.sender.post_login(user), loop=evt.loop)
        evt.sender.command_status = None
        return await evt.reply(f"Successfully registered to Telegram.")
    except PhoneNumberOccupiedError:
        return await evt.reply("That phone number has already been registered. "
                               "You can log in with `$cmdprefix+sp login`.")
    except FirstNameInvalidError:
        return await evt.reply("Invalid name. Please set a Matrix displayname before registering.")
    except PhoneCodeExpiredError:
        return await evt.reply(
            "Phone code expired. Try again with `$cmdprefix+sp register <phone>`.")
    except PhoneCodeInvalidError:
        return await evt.reply("Invalid phone code.")
    except Exception:
        evt.log.exception("Error sending phone code")
        return await evt.reply("Unhandled exception while sending code. "
                               "Check console for more details.")


@command_handler(needs_auth=False, management_only=True)
async def login(evt):
    if evt.sender.logged_in:
        return await evt.reply("You are already logged in.")

    allow_matrix_login = evt.config.get("bridge.allow_matrix_login", True)
    if allow_matrix_login:
        evt.sender.command_status = {
            "next": enter_phone,
            "action": "Login",
        }

    if evt.config["appservice.public.enabled"]:
        prefix = evt.config["appservice.public.external"]
        url = f"{prefix}/login?mxid={evt.sender.mxid}"
        if evt.config.get("bridge.allow_matrix_login", True):
            return await evt.reply("\n\n".join((
                "This bridge instance allows you to log in inside or outside Matrix.",
                "If you would like to log in within Matrix, please send your phone number here.",
                f"If you would like to log in outside of Matrix, [click here]({url}).")))
        return await evt.reply("This bridge instance does not allow logging in inside Matrix.\n\n"
                               f"Please visit [the login page]({url}) to log in.")
    elif allow_matrix_login:
        return await evt.reply(
            "This bridge instance does not allow you to log in outside of Matrix.\n\n"
            "Please send your phone number here to start the login process.")
    return await evt.reply("This bridge instance has been configured to not allow logging in.")


async def request_code(evt, phone_number, next_status):
    ok = False
    try:
        await evt.sender.ensure_started(even_if_no_session=True)
        await evt.sender.client.sign_in(phone_number)
        ok = True
        return await evt.reply(f"Login code sent to {phone_number}. Please send the code here.")
    except PhoneNumberAppSignupForbiddenError:
        return await evt.reply(
            "Your phone number does not allow 3rd party apps to sign in.")
    except PhoneNumberFloodError:
        return await evt.reply(
            "Your phone number has been temporarily blocked for flooding. "
            "The ban is usually applied for around a day.")
    except FloodWaitError as e:
        return await evt.reply(
            "Your phone number has been temporarily blocked for flooding. "
            f"Please wait for {format_duration(e.seconds)} before trying again.")
    except PhoneNumberBannedError:
        return await  evt.reply("Your phone number has been banned from Telegram.")
    except PhoneNumberUnoccupiedError:
        return await  evt.reply("That phone number has not been registered. "
                                "Please register with `$cmdprefix+sp register <phone>`.")
    except Exception:
        evt.log.exception("Error requesting phone code")
        return await evt.reply("Unhandled exception while requesting code. "
                               "Check console for more details.")
    finally:
        evt.sender.command_status = next_status if ok else None


@command_handler(needs_auth=False)
async def enter_phone(evt):
    if len(evt.args) == 0:
        return await evt.reply("**Usage:** `$cmdprefix+sp enter-phone <phone>`")
    elif not evt.config.get("bridge.allow_matrix_login", True):
        return await evt.reply("This bridge instance does not allow in-Matrix login. "
                               "Please use `$cmdprefix+sp login` to get login instructions")

    phone_number = evt.args[0]
    await request_code(evt, phone_number, {
        "next": enter_code,
        "action": "Login",
    })


@command_handler(needs_auth=False)
async def enter_code(evt):
    if len(evt.args) == 0:
        return await evt.reply("**Usage:** `$cmdprefix+sp enter-code <code>`")
    elif not evt.config.get("bridge.allow_matrix_login", True):
        return await evt.reply("This bridge instance does not allow in-Matrix login. "
                               "Please use `$cmdprefix+sp login` to get login instructions")
    try:
        await evt.sender.ensure_started(even_if_no_session=True)
        user = await evt.sender.client.sign_in(code=evt.args[0])
        asyncio.ensure_future(evt.sender.post_login(user), loop=evt.loop)
        evt.sender.command_status = None
        return await evt.reply(f"Successfully logged in as @{user.username}")
    except PhoneCodeExpiredError:
        return await evt.reply("Phone code expired. Try again with `$cmdprefix+sp login`.")
    except PhoneCodeInvalidError:
        return await evt.reply("Invalid phone code.")
    except SessionPasswordNeededError:
        evt.sender.command_status = {
            "next": enter_password,
            "action": "Login (password entry)",
        }
        return await evt.reply("Your account has two-factor authentication. "
                               "Please send your password here.")
    except Exception:
        evt.log.exception("Error sending phone code")
        return await evt.reply("Unhandled exception while sending code. "
                               "Check console for more details.")


@command_handler(needs_auth=False)
async def enter_password(evt):
    if len(evt.args) == 0:
        return await evt.reply("**Usage:** `$cmdprefix+sp enter-password <password>`")
    elif not evt.config.get("bridge.allow_matrix_login", True):
        return await evt.reply("This bridge instance does not allow in-Matrix login. "
                               "Please use `$cmdprefix+sp login` to get login instructions")
    try:
        await evt.sender.ensure_started(even_if_no_session=True)
        user = await evt.sender.client.sign_in(password=" ".join(evt.args))
        asyncio.ensure_future(evt.sender.post_login(user), loop=evt.loop)
        evt.sender.command_status = None
        return await evt.reply(f"Successfully logged in as @{user.username}")
    except PasswordHashInvalidError:
        return await evt.reply("Incorrect password.")
    except Exception:
        evt.log.exception("Error sending password")
        return await evt.reply("Unhandled exception while sending password. "
                               "Check console for more details.")


@command_handler(needs_auth=False)
async def logout(evt):
    if not evt.sender.logged_in:
        return await evt.reply("You're not logged in.")
    if await evt.sender.log_out():
        return await evt.reply("Logged out successfully.")
    return await evt.reply("Failed to log out.")
