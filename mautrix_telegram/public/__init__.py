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
from aiohttp import web
from mako.template import Template
import asyncio
import pkg_resources
import logging

from telethon.errors import *

from ..user import User
from ..commands.auth import enter_password
from ..util import format_duration


class PublicBridgeWebsite:
    log = logging.getLogger("mau.public")

    def __init__(self, loop):
        self.loop = loop

        self.login = Template(
            pkg_resources.resource_string("mautrix_telegram", "public/login.html.mako"))

        self.app = web.Application(loop=loop)
        self.app.router.add_route("GET", "/login", self.get_login)
        self.app.router.add_route("POST", "/login", self.post_login)
        self.app.router.add_static("/",
                                   pkg_resources.resource_filename("mautrix_telegram", "public/"))

    async def get_login(self, request):
        user = (User.get_by_mxid(request.rel_url.query["mxid"], create=False)
                if "mxid" in request.rel_url.query else None)
        if not user:
            return self.render_login(
                mxid=request.rel_url.query["mxid"] if "mxid" in request.rel_url.query else None,
                state="request")
        elif not user.whitelisted:
            return self.render_login(mxid=user.mxid, error="You are not whitelisted.", status=403)
        await user.ensure_started()
        if not user.logged_in:
            return self.render_login(mxid=user.mxid, state="request")

        return self.render_login(mxid=user.mxid, username=user.username)

    def render_login(self, status=200, username="", state="", error="", message="", mxid=""):
        return web.Response(status=status, content_type="text/html",
                            text=self.login.render(username=username, state=state, error=error,
                                                   message=message, mxid=mxid))

    async def post_login_phone(self, user, phone):
        try:
            await user.client.sign_in(phone or "+123")
            return self.render_login(mxid=user.mxid, state="code", status=200,
                                     message="Code requested successfully.")
        except PhoneNumberInvalidError:
            return self.render_login(mxid=user.mxid, state="request", status=400,
                                     error="Invalid phone number.")
        except PhoneNumberUnoccupiedError:
            return self.render_login(mxid=user.mxid, state="request", status=404,
                                     error="That phone number has not been registered.")
        except PhoneNumberFloodError:
            return self.render_login(
                mxid=user.mxid, state="request", status=429,
                error="Your phone number has been temporarily blocked for flooding. "
                      "The ban is usually applied for around a day.")
        except FloodWaitError as e:
            return self.render_login(
                mxid=user.mxid, state="request", status=429,
                error="Your phone number has been temporarily blocked for flooding. "
                      f"Please wait for {format_duration(e.seconds)} before trying again.")
        except PhoneNumberBannedError:
            return self.render_login(mxid=user.mxid, state="request", status=401,
                                     error="Your phone number is banned from Telegram.")
        except PhoneNumberAppSignupForbiddenError:
            return self.render_login(mxid=user.mxid, state="request", status=401,
                                     error="You have disabled 3rd party apps on your account.")
        except Exception:
            self.log.exception("Error requesting phone code")
            return self.render_login(mxid=user.mxid, state="request", status=500,
                                     error="Internal server error while requesting code.")

    async def post_login_code(self, user, code, password_in_data):
        try:
            user_info = await user.client.sign_in(code=code)
            asyncio.ensure_future(user.post_login(user_info), loop=self.loop)
            if user.command_status and user.command_status["action"] == "Login":
                user.command_status = None
            return self.render_login(mxid=user.mxid, state="logged-in", status=200,
                                     username=user_info.username)
        except PhoneCodeInvalidError:
            return self.render_login(mxid=user.mxid, state="code", status=403,
                                     error="Incorrect phone code.")
        except PhoneCodeExpiredError:
            return self.render_login(mxid=user.mxid, state="code", status=403,
                                     error="Phone code expired.")
        except SessionPasswordNeededError:
            if not password_in_data:
                if user.command_status and user.command_status["action"] == "Login":
                    user.command_status = {
                        "next": enter_password,
                        "action": "Login (password entry)",
                    }
                return self.render_login(
                    mxid=user.mxid, state="password", status=200,
                    message="Code accepted, but you have 2-factor authentication is enabled.")
            return None
        except Exception:
            self.log.exception("Error sending phone code")
            return self.render_login(mxid=user.mxid, state="code", status=500,
                                     error="Internal server error while sending code.")

    async def post_login_password(self, user, password):
        try:
            user_info = await user.client.sign_in(password=password)
            asyncio.ensure_future(user.post_login(user_info), loop=self.loop)
            if user.command_status and user.command_status["action"] == "Login (password entry)":
                user.command_status = None
            return self.render_login(mxid=user.mxid, state="logged-in", status=200,
                                     username=user_info.username)
        except (PasswordHashInvalidError, PasswordEmptyError):
            return self.render_login(mxid=user.mxid, state="password", status=400,
                                     error="Incorrect password.")
        except Exception:
            self.log.exception("Error sending password")
            return self.render_login(mxid=user.mxid, state="password", status=500,
                                     error="Internal server error while sending password.")

    async def post_login(self, request):
        data = await request.post()
        if "mxid" not in data:
            return self.render_login(error="Please enter your Matrix ID.", status=400)

        user = await User.get_by_mxid(data["mxid"]).ensure_started(even_if_no_session=True)
        if not user.whitelisted:
            return self.render_login(mxid=user.mxid, error="You are not whitelisted.", status=403)
        elif user.logged_in:
            return self.render_login(mxid=user.mxid, username=user.username)

        if "phone" in data:
            return await self.post_login_phone(user, data["phone"])
        elif "code" in data:
            resp = await self.post_login_code(user, data["code"],
                                              password_in_data="password" in data)
            if resp or "password" not in data:
                return resp
        elif "password" not in data:
            return self.render_login(error="No data given.", status=400)

        if "password" in data:
            return await self.post_login_password(user, data["password"])
        return self.render_login(error="This should never happen.", status=500)
