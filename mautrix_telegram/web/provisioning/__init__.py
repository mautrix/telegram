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
import logging

from ..common import AuthAPI


class ProvisioningAPI(AuthAPI):
    log = logging.getLogger("mau.web.provisioning")

    def __init__(self, loop):
        super(AuthAPI, self).__init__(loop)

        self.app = web.Application(loop=loop)

        login_prefix = "/login/{mxid:@[^:]*:.+}"
        self.app.router.add_route("POST", f"{login_prefix}/bot_token", self.send_bot_token)
        self.app.router.add_route("POST", f"{login_prefix}/request_code", self.request_code)
        self.app.router.add_route("POST", f"{login_prefix}/send_code", self.send_code)
        self.app.router.add_route("POST", f"{login_prefix}/send_password", self.send_password)

    def get_login_response(self, status=200, state="", username="", mxid="", message="", error="",
                           errcode=""):
        if username:
            resp = {
                "state": "logged-in",
                "username": username,
            }
        elif message:
            resp = {
                "message": message
            }
        else:
            resp = {
                "error": error,
                "errcode": errcode,
            }
        return web.json_response(resp, status=status)

    async def get_user(self, request: web.Request):
        mxid = request.match_info["mxid"]
        user = await User.get_by_mxid(mxid).ensure_started(even_if_no_session=True)
        if not user.puppet_whitelisted:
            return user, self.get_login_response(mxid=user.mxid, error="You are not whitelisted.",
                                                 errcode="mxid_not_whitelisted", status=403)
        elif await user.is_logged_in():
            return user, self.get_login_response(mxid=user.mxid, username=user.username, status=409)
        return user, None

    async def send_bot_token(self, request: web.Request):
        user, err = await self.get_user(request)
        if err:
            return err
        data = await request.json()
        return await self.post_login_token(user, data.get("token", ""))

    async def request_code(self, request: web.Request):
        user, err = await self.get_user(request)
        if err:
            return err
        data = await request.json()
        return await self.post_login_phone(user, data.get("phone", ""))

    async def send_code(self, request: web.Request):
        user, err = await self.get_user(request)
        if err:
            return err
        data = await request.json()
        return await self.post_login_code(user, data.get("code", 0), password_in_data=False)

    async def send_password(self, request: web.Request):
        user, err = await self.get_user(request)
        if err:
            return err
        data = await request.json()
        return await self.post_login_password(user, data.get("password", ""))
