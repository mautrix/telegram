# -*- coding: future_fstrings -*-
# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2018 Tulir Asokan
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
import asyncio

from telethon.errors import *

from . import command_handler


@command_handler(needs_auth=False)
async def ping(evt):
    if not evt.sender.logged_in:
        return await evt.reply("You're not logged in.")
    me = await evt.sender.client.get_me()
    if me:
        return await evt.reply(f"You're logged in as @{me.username}")
    else:
        return await evt.reply("You're not logged in.")


@command_handler(needs_auth=False, management_only=True)
def register(evt):
    return evt.reply("Not yet implemented.")


@command_handler(needs_auth=False, management_only=True)
async def login(evt):
    if evt.sender.logged_in:
        return await evt.reply("You are already logged in.")
    elif len(evt.args) == 0:
        return await evt.reply("**Usage:** `$cmdprefix+sp login <phone number>`")
    phone_number = evt.args[0]
    await evt.sender.ensure_started(even_if_no_session=True)
    await evt.sender.client.sign_in(phone_number)
    evt.sender.command_status = {
        "next": enter_code,
        "action": "Login",
    }
    return await evt.reply(f"Login code sent to {phone_number}. Please send the code here.")


@command_handler(needs_auth=False)
async def enter_code(evt):
    if len(evt.args) == 0:
        return await evt.reply("**Usage:** `$cmdprefix+sp enter-code <code>`")

    try:
        await evt.sender.ensure_started(even_if_no_session=True)
        user = await evt.sender.client.sign_in(code=evt.args[0])
        asyncio.ensure_future(evt.sender.post_login(user), loop=evt.loop)
        evt.sender.command_status = None
        return await evt.reply(f"Successfully logged in as @{user.username}")
    except PhoneNumberUnoccupiedError:
        return await evt.reply("That phone number has not been registered."
                               "Please register with `$cmdprefix+sp register <phone>`.")
    except PhoneCodeExpiredError:
        return await evt.reply(
            "Phone code expired. Try again with `$cmdprefix+sp login <phone>`.")
    except PhoneCodeInvalidError:
        return await evt.reply("Invalid phone code.")
    except PhoneNumberAppSignupForbiddenError:
        return await evt.reply(
            "Your phone number does not allow 3rd party apps to sign in.")
    except PhoneNumberFloodError:
        return await evt.reply(
            "Your phone number has been temporarily blocked for flooding. "
            "The block is usually applied for around a day.")
    except PhoneNumberBannedError:
        return await evt.reply("Your phone number has been banned from Telegram.")
    except SessionPasswordNeededError:
        evt.sender.command_status = {
            "next": enter_password,
            "action": "Login (password entry)",
        }
        return await evt.reply("Your account has two-factor authentication."
                               "Please send your password here.")
    except Exception:
        evt.log.exception("Error sending phone code")
        return await evt.reply("Unhandled exception while sending code."
                               "Check console for more details.")


@command_handler(needs_auth=False)
async def enter_password(evt):
    if len(evt.args) == 0:
        return await evt.reply("**Usage:** `$cmdprefix+sp enter-password <password>`")

    try:
        await evt.sender.ensure_started(even_if_no_session=True)
        user = await evt.sender.client.sign_in(password=evt.args[0])
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
