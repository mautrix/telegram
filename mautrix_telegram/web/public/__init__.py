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
from typing import Optional
from aiohttp import web
from mako.template import Template
import pkg_resources
import asyncio
import logging
import random
import string
import time

from ...types import MatrixUserID
from ...util import sign_token, verify_token
from ...user import User
from ...puppet import Puppet
from ..common import AuthAPI


class PublicBridgeWebsite(AuthAPI):
    log = logging.getLogger("mau.web.public")  # type: logging.Logger

    def __init__(self, loop: asyncio.AbstractEventLoop):
        super().__init__(loop)
        self.secret_key = "".join(
            random.choice(string.ascii_lowercase + string.digits) for _ in range(64))  # type: str

        self.login = Template(pkg_resources.resource_string(
            "mautrix_telegram", "web/public/login.html.mako"))  # type: Template

        self.mx_login = Template(pkg_resources.resource_string(
            "mautrix_telegram", "web/public/matrix-login.html.mako"))  # type: Template

        self.app = web.Application(loop=loop)  # type: web.Application
        self.app.router.add_route("GET", "/login", self.get_login)
        self.app.router.add_route("POST", "/login", self.post_login)
        self.app.router.add_route("GET", "/matrix-login", self.get_matrix_login)
        self.app.router.add_route("POST", "/matrix-login", self.post_matrix_login)
        self.app.router.add_static("/", pkg_resources.resource_filename("mautrix_telegram",
                                                                        "web/public/"))

    def make_token(self, mxid: str, endpoint: str = "/login", expires_in: int = 900) -> str:
        return sign_token(self.secret_key, {
            "mxid": mxid,
            "endpoint": endpoint,
            "expiry": int(time.time()) + expires_in,
        })

    def verify_token(self, token: str, endpoint: str = "/login") -> Optional[MatrixUserID]:
        token = verify_token(self.secret_key, token)
        if token and (token.get("expiry", 0) > int(time.time()) and
                      token.get("endpoint", None) == endpoint):
            return MatrixUserID(token.get("mxid", None))
        return None

    async def get_login(self, request: web.Request) -> web.Response:
        state = "bot_token" if request.rel_url.query.get("mode", "") == "bot" else "request"

        mxid = self.verify_token(request.rel_url.query.get("token", None), endpoint="/login")
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

        return self.get_login_response(mxid=user.mxid, human_tg_id=user.human_tg_id)

    async def get_matrix_login(self, request: web.Request) -> web.Response:
        mxid = self.verify_token(request.rel_url.query.get("token", None),
                                 endpoint="/matrix-login")
        if not mxid:
            return self.get_mx_login_response(status=401, state="invalid-token")
        user = User.get_by_mxid(mxid, create=False) if mxid else None

        if not user:
            return self.get_mx_login_response(mxid=mxid)
        elif not user.puppet_whitelisted:
            return self.get_mx_login_response(mxid=user.mxid, error="You are not whitelisted.",
                                              status=403)
        await user.ensure_started()
        if not await user.is_logged_in():
            return self.get_mx_login_response(mxid=user.mxid, status=403,
                                              error="You are not logged in to Telegram.")

        puppet = Puppet.get(user.tgid)
        if puppet.is_real_user:
            return self.get_mx_login_response(state="already-logged-in", status=409)

        return self.get_mx_login_response(mxid=user.mxid)

    def get_login_response(self, status: int = 200, state: str = "", username: str = "",
                           phone: str = "", human_tg_id: str = "", mxid: str = "",
                           message: str = "", error: str = "", errcode: str = "") -> web.Response:
        return web.Response(status=status, content_type="text/html",
                            text=self.login.render(human_tg_id=human_tg_id, state=state,
                                                   error=error, message=message, mxid=mxid))

    def get_mx_login_response(self, status: int = 200, state: str = "", username: str = "",
                              phone: str = "", human_tg_id: str = "", mxid: str = "",
                              message: str = "", error: str = "", errcode: str = ""
                              ) -> web.Response:
        return web.Response(status=status, content_type="text/html",
                            text=self.mx_login.render(human_tg_id=human_tg_id, state=state,
                                                      error=error, message=message, mxid=mxid))

    async def post_matrix_login(self, request: web.Request) -> web.Response:
        mxid = self.verify_token(request.rel_url.query.get("token", None),
                                 endpoint="/matrix-login")
        if not mxid:
            return self.get_mx_login_response(status=401, state="invalid-token")

        data = await request.post()

        user = await User.get_by_mxid(mxid).ensure_started()
        if not user.puppet_whitelisted:
            return self.get_mx_login_response(mxid=user.mxid, error="You are not whitelisted.",
                                              status=403)
        elif not await user.is_logged_in():
            return self.get_mx_login_response(mxid=user.mxid, status=403,
                                              error="You are not logged in to Telegram.")
        mode = data.get("mode", "access_token")
        if mode == "password":
            return await self.post_matrix_password(user, data["value"])
        elif mode == "access_token":
            return await self.post_matrix_token(user, data["value"])
        return self.get_mx_login_response(mxid=user.mxid, status=400,
                                          error="You must provide an access token or "
                                                "password.")

    async def post_login(self, request: web.Request) -> web.Response:
        mxid = self.verify_token(request.rel_url.query.get("token", None), endpoint="/login")
        if not mxid:
            return self.get_login_response(status=401, state="invalid-token")

        data = await request.post()

        user = await User.get_by_mxid(mxid).ensure_started(even_if_no_session=True)
        if not user.puppet_whitelisted:
            return self.get_login_response(mxid=user.mxid, error="You are not whitelisted.",
                                           status=403)
        elif await user.is_logged_in():
            return self.get_login_response(mxid=user.mxid, human_tg_id=user.human_tg_id)

        await user.ensure_started(even_if_no_session=True)

        if "phone" in data:
            return await self.post_login_phone(user, data["phone"])
        elif "bot_token" in data:
            return await self.post_login_token(user, data["bot_token"])
        elif "code" in data:
            try:
                code = int(data["code"].strip())
            except ValueError:
                return self.get_login_response(mxid=user.mxid, state="code", status=400,
                                               errcode="phone_code_invalid",
                                               error="Phone code must be a number.")
            resp = await self.post_login_code(user, code,
                                              password_in_data="password" in data)
            if resp or "password" not in data:
                return resp
        elif "password" not in data:
            return self.get_login_response(error="No data given.", status=400)

        if "password" in data:
            return await self.post_login_password(user, data["password"])
        return self.get_login_response(error="This should never happen.", status=500)
