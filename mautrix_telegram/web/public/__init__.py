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

from ...user import User
from ..common import AuthAPI


class PublicBridgeWebsite(AuthAPI):
    log = logging.getLogger("mau.public")

    def __init__(self, loop):
        super(AuthAPI, self).__init__(loop)

        self.login = Template(
            pkg_resources.resource_string("mautrix_telegram", "public/login.html.mako"))

        self.app = web.Application(loop=loop)
        self.app.router.add_route("GET", "/login", self.get_login)
        self.app.router.add_route("POST", "/login", self.post_login)
        self.app.router.add_static("/",
                                   pkg_resources.resource_filename("mautrix_telegram", "public/"))

    async def get_login(self, request):
        state = "token" if request.rel_url.query.get("mode", "") == "bot" else "request"

        mxid = request.rel_url.query.get("mxid", None)
        user = User.get_by_mxid(mxid, create=False) if mxid else None

        if not user:
            return self.get_login_response(mxid=mxid, state=state)
        elif not user.puppet_whitelisted:
            return self.get_login_response(mxid=user.mxid, error="You are not whitelisted.",
                                           status=403)
        await user.ensure_started()
        if not await user.is_logged_in():
            return self.get_login_response(mxid=user.mxid, state=state)

        return self.get_login_response(mxid=user.mxid, username=user.username)

    def get_login_response(self, status=200, state="", username="", mxid="", message="", error="",
                           errcode=""):
        return web.Response(status=status, content_type="text/html",
                            text=self.login.render(username=username, state=state, error=error,
                                                   message=message, mxid=mxid))

    async def post_login_token(self, user, token):
        try:
            user_info = await user.client.sign_in(bot_token=token)
            asyncio.ensure_future(user.post_login(user_info), loop=self.loop)
            if user.command_status and user.command_status["action"] == "Login":
                user.command_status = None
            return self.get_login_response(mxid=user.mxid, state="logged-in", status=200,
                                           username=user_info.username)
        except Exception:
            self.log.exception("Error sending bot token")
            return self.get_login_response(mxid=user.mxid, state="token", status=500,
                                           error="Internal server error while sending token.")

    async def post_login(self, request):
        data = await request.post()
        if "mxid" not in data:
            return self.get_login_response(error="Please enter your Matrix ID.", status=400)

        user = await User.get_by_mxid(data["mxid"]).ensure_started(even_if_no_session=True)
        if not user.puppet_whitelisted:
            return self.get_login_response(mxid=user.mxid, error="You are not whitelisted.",
                                           status=403)
        elif await user.is_logged_in():
            return self.get_login_response(mxid=user.mxid, username=user.username)

        await user.ensure_started(even_if_no_session=True)

        if "phone" in data:
            return await self.post_login_phone(user, data["phone"])
        elif "token" in data:
            return await self.post_login_token(user, data["token"])
        elif "code" in data:
            resp = await self.post_login_code(user, data["code"],
                                              password_in_data="password" in data)
            if resp or "password" not in data:
                return resp
        elif "password" not in data:
            return self.get_login_response(error="No data given.", status=400)

        if "password" in data:
            return await self.post_login_password(user, data["password"])
        return self.get_login_response(error="This should never happen.", status=500)
