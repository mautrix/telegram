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
import pkg_resources
import logging
import random
import string
import time

from ...util import sign_token, verify_token
from ...user import User
from ..common import AuthAPI


class PublicBridgeWebsite(AuthAPI):
    log = logging.getLogger("mau.web.public")

    def __init__(self, loop):
        super().__init__(loop)
        self.secret_key = "".join(
            random.choice(string.ascii_lowercase + string.digits) for _ in range(64))

        self.login = Template(
            pkg_resources.resource_string("mautrix_telegram", "web/public/login.html.mako"))

        self.app = web.Application(loop=loop)
        self.app.router.add_route("GET", "/login", self.get_login)
        self.app.router.add_route("POST", "/login", self.post_login)
        self.app.router.add_static("/", pkg_resources.resource_filename("mautrix_telegram",
                                                                        "web/public/"))

    def make_token(self, mxid, expires_in=900):
        return sign_token(self.secret_key, {
            "mxid": mxid,
            "expiry": int(time.time()) + expires_in,
        })

    def verify_token(self, token):
        token = verify_token(self.secret_key, token)
        if token and token.get("expiry", 0) > int(time.time()):
            return token.get("mxid", None)
        return None

    async def get_login(self, request):
        state = "bot_token" if request.rel_url.query.get("mode", "") == "bot" else "request"

        mxid = self.verify_token(request.rel_url.query.get("token", None))
        if not mxid:
            return self.get_login_response(status=401, state="invalid-token")
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

    async def post_login(self, request):
        mxid = self.verify_token(request.rel_url.query.get("token", None))
        if not mxid:
            return self.get_login_response(status=401, state="invalid-token")

        data = await request.post()

        user = await User.get_by_mxid(mxid).ensure_started(even_if_no_session=True)
        if not user.puppet_whitelisted:
            return self.get_login_response(mxid=user.mxid, error="You are not whitelisted.",
                                           status=403)
        elif await user.is_logged_in():
            return self.get_login_response(mxid=user.mxid, username=user.username)

        await user.ensure_started(even_if_no_session=True)

        if "phone" in data:
            return await self.post_login_phone(user, data["phone"])
        elif "bot_token" in data:
            return await self.post_login_token(user, data["bot_token"])
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
