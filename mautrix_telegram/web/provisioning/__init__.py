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
from typing import Tuple, Optional, Callable, Awaitable
import logging
import json

from telethon.utils import get_peer_id

from ...user import User
from ...portal import Portal
from ..common import AuthAPI


class ProvisioningAPI(AuthAPI):
    log = logging.getLogger("mau.web.provisioning")

    def __init__(self, config, loop):
        super().__init__(loop)
        self.secret = config["appservice.provisioning.shared_secret"]

        self.app = web.Application(loop=loop, middlewares=[self.error_middleware])

        portal_prefix = "/portal/{mxid:![^/]+}"
        self.app.router.add_route("GET", f"{portal_prefix}", self.get_portal)
        # self.app.router.add_route("POST", portal_prefix + "/connect/{chat_id:[0-9]+}",
        #                          self.connect_chat)
        # self.app.router.add_route("POST", f"{portal_prefix}/create", self.create_chat)
        # self.app.router.add_route("POST", f"{portal_prefix}/disconnect", self.disconnect_chat)

        user_prefix = "/user/{mxid:@[^:]*:[^/]+}"
        self.app.router.add_route("GET", f"{user_prefix}", self.get_me)
        self.app.router.add_route("GET", f"{user_prefix}/chats", self.get_chats)

        self.app.router.add_route("POST", f"{user_prefix}/send_bot_token", self.send_bot_token)
        self.app.router.add_route("POST", f"{user_prefix}/request_code", self.request_code)
        self.app.router.add_route("POST", f"{user_prefix}/send_code", self.send_code)
        self.app.router.add_route("POST", f"{user_prefix}/send_password", self.send_password)

    async def get_portal(self, request: web.Request) -> web.Response:
        mxid = request.match_info["mxid"]
        portal = Portal.get_by_mxid(mxid)
        if not portal:
            return self.get_error_response(404, "room_not_found",
                                           "Portal with given Matrix ID not found.")
        return web.json_response({
            "mxid": portal.mxid,
            "chat_id": get_peer_id(portal.peer),
            "peer_type": portal.peer_type,
            "title": portal.title,
            "about": portal.about,
            "username": portal.username,
            "megagroup": portal.megagroup,
        })

    async def get_me(self, request: web.Request) -> web.Response:
        data, user, err = await self.get_user_request_info(request, require_logged_in=True)
        if err is not None:
            return err

        me = await user.client.get_me()
        return web.json_response({
            "username": me.username,
            "first_name": me.first_name,
            "last_name": me.last_name,
            "phone": me.phone,
            "is_bot": me.bot,
        })

    async def get_chats(self, request: web.Request) -> web.Response:
        data, user, err = await self.get_user_request_info(request, require_logged_in=True)
        if err is not None:
            return err

        if not user.is_bot:
            chats = await user.get_dialogs()
            return web.json_response([{
                "id": get_peer_id(chat),
                "title": chat.title,
            } for chat in chats])
        else:
            return web.json_response([{
                "id": get_peer_id(chat.peer),
                "title": chat.title,
            } for chat in user.portals.values() if chat.tgid])

    async def send_bot_token(self, request: web.Request) -> web.Response:
        data, user, err = await self.get_user_request_info(request)
        if err is not None:
            return err
        return await self.post_login_token(user, data.get("token", ""))

    async def request_code(self, request: web.Request) -> web.Response:
        data, user, err = await self.get_user_request_info(request)
        if err is not None:
            return err
        return await self.post_login_phone(user, data.get("phone", ""))

    async def send_code(self, request: web.Request) -> web.Response:
        data, user, err = await self.get_user_request_info(request)
        if err is not None:
            return err
        return await self.post_login_code(user, data.get("code", 0), password_in_data=False)

    async def send_password(self, request: web.Request) -> web.Response:
        data, user, err = await self.get_user_request_info(request)
        if err is not None:
            return err
        return await self.post_login_password(user, data.get("password", ""))

    @staticmethod
    async def error_middleware(_, handler) -> Callable[[web.Request], Awaitable[web.Response]]:
        async def middleware_handler(request: web.Request) -> web.Response:
            try:
                return await handler(request)
            except web.HTTPException as ex:
                return web.json_response({
                    "error": f"Unhandled HTTP {ex.status}",
                    "errcode": f"unhandled_http_{ex.status}",
                }, status=ex.status)

        return middleware_handler

    @staticmethod
    def get_error_response(status=200, errcode="", error="") -> web.Response:
        return web.json_response({
            "error": error,
            "errcode": errcode,
        }, status=status)

    def get_login_response(self, status=200, state="", username="", mxid="", message="", error="",
                           errcode="") -> web.Response:
        if username:
            resp = {
                "state": "logged-in",
                "username": username,
            }
        elif message:
            resp = {
                "state": state,
                "message": message,
            }
        else:
            resp = {
                "state": state,
                "error": error,
                "errcode": errcode,
            }
        return web.json_response(resp, status=status)

    def check_authorization(self, request: web.Request) -> bool:
        return request.headers.get("Authorization", "") == f"Bearer {self.secret}"

    @staticmethod
    async def get_data(request: web.Request) -> Optional[dict]:
        try:
            return await request.json()
        except json.JSONDecodeError:
            return None

    async def get_user(self, mxid: str, require_logged_in: bool = False
                       ) -> Tuple[Optional[User], Optional[web.Response]]:
        user = await User.get_by_mxid(mxid).ensure_started(even_if_no_session=True)
        if not user.puppet_whitelisted:
            return user, self.get_login_response(error="You are not whitelisted.",
                                                 errcode="mxid_not_whitelisted", status=403)
        logged_in = await user.is_logged_in()
        if not require_logged_in and logged_in:
            return user, self.get_login_response(username=user.username, status=409,
                                                 error="You are already logged in.",
                                                 errcode="already_logged_in")
        elif require_logged_in and not logged_in:
            return user, self.get_login_response(status=403, error="You are not logged in.",
                                                 errcode="not_logged_in")
        return user, None

    async def get_user_request_info(self, request: web.Request, require_logged_in: bool = False
                                    ) -> (Tuple[Optional[dict],
                                                Optional[User],
                                                Optional[web.Response]]):
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {self.secret}":
            return None, None, self.get_login_response(error="Shared secret is not valid.",
                                                       errcode="shared_secret_invalid",
                                                       status=401)

        data = None
        if request.method == "POST" or request.method == "PUT":
            data = await self.get_data(request)
            if not data:
                return None, None, self.get_login_response(error="Invalid JSON.",
                                                           errcode="json_invalid", status=400)

        mxid = request.match_info["mxid"]
        user, err = await self.get_user(mxid, require_logged_in)

        return data, user, err
