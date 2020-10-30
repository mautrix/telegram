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
from typing import Awaitable, Callable, Dict, Optional, Tuple, TYPE_CHECKING
import asyncio
import logging
import json

from aiohttp import web

from telethon.utils import get_peer_id, resolve_id
from telethon.tl.types import ChatForbidden, ChannelForbidden, TypeChat

from mautrix.appservice import AppService
from mautrix.errors import MatrixRequestError, IntentError
from mautrix.types import UserID

from ...types import TelegramID
from ...user import User
from ...portal import Portal
from ...commands.portal.util import user_has_power_level, get_initial_state
from ..common import AuthAPI

if TYPE_CHECKING:
    from ...context import Context


class ProvisioningAPI(AuthAPI):
    log: logging.Logger = logging.getLogger("mau.web.provisioning")
    secret: str
    az: AppService
    context: 'Context'
    app: web.Application

    def __init__(self, context: "Context") -> None:
        super().__init__(context.loop)
        self.secret = context.config["appservice.provisioning.shared_secret"]
        self.az = context.az
        self.context = context

        self.app = web.Application(loop=context.loop, middlewares=[self.error_middleware])

        portal_prefix = "/portal/{mxid:![^/]+}"
        self.app.router.add_route("GET", f"{portal_prefix}", self.get_portal_by_mxid)
        self.app.router.add_route("GET", "/portal/{tgid:-[0-9]+}", self.get_portal_by_tgid)
        self.app.router.add_route("POST", portal_prefix + "/connect/{chat_id:-[0-9]+}",
                                  self.connect_chat)
        self.app.router.add_route("POST", f"{portal_prefix}/create", self.create_chat)
        self.app.router.add_route("POST", f"{portal_prefix}/disconnect", self.disconnect_chat)

        user_prefix = "/user/{mxid:@[^:]*:[^/]+}"
        self.app.router.add_route("GET", f"{user_prefix}", self.get_user_info)
        self.app.router.add_route("GET", f"{user_prefix}/chats", self.get_chats)

        self.app.router.add_route("POST", f"{user_prefix}/logout", self.logout)
        self.app.router.add_route("POST", f"{user_prefix}/login/bot_token", self.send_bot_token)
        self.app.router.add_route("POST", f"{user_prefix}/login/request_code", self.request_code)
        self.app.router.add_route("POST", f"{user_prefix}/login/send_code", self.send_code)
        self.app.router.add_route("POST", f"{user_prefix}/login/send_password", self.send_password)

        self.app.router.add_route("GET", "/bridge", self.bridge_info)

    async def get_portal_by_mxid(self, request: web.Request) -> web.Response:
        err = self.check_authorization(request)
        if err is not None:
            return err

        mxid = request.match_info["mxid"]
        portal = Portal.get_by_mxid(mxid)
        if not portal:
            return self.get_error_response(404, "portal_not_found",
                                           "Portal with given Matrix ID not found.")
        return await self._get_portal_response(UserID(request.query.get("user_id", "")), portal)

    async def get_portal_by_tgid(self, request: web.Request) -> web.Response:
        err = self.check_authorization(request)
        if err is not None:
            return err

        try:
            tgid, _ = resolve_id(int(request.match_info["tgid"]))
        except ValueError:
            return self.get_error_response(400, "tgid_invalid",
                                           "Given chat ID is not valid.")
        portal = Portal.get_by_tgid(tgid)
        if not portal:
            return self.get_error_response(404, "portal_not_found",
                                           "Portal to given Telegram chat not found.")
        return await self._get_portal_response(UserID(request.query.get("user_id", "")), portal)

    async def _get_portal_response(self, user_id: UserID, portal: Portal) -> web.Response:
        user, _ = await self.get_user(user_id, expect_logged_in=None, require_puppeting=False)
        return web.json_response({
            "mxid": portal.mxid,
            "chat_id": get_peer_id(portal.peer),
            "peer_type": portal.peer_type,
            "title": portal.title,
            "about": portal.about,
            "username": portal.username,
            "megagroup": portal.megagroup,
            "can_unbridge": (await portal.can_user_perform(user, "unbridge")) if user else False,
        })

    async def connect_chat(self, request: web.Request) -> web.Response:
        err = self.check_authorization(request)
        if err is not None:
            return err

        room_id = request.match_info["mxid"]
        if Portal.get_by_mxid(room_id):
            return self.get_error_response(409, "room_already_bridged",
                                           "Room is already bridged to another Telegram chat.")

        chat_id = request.match_info["chat_id"]
        if chat_id.startswith("-100"):
            tgid = TelegramID(int(chat_id[4:]))
            peer_type = "channel"
        elif chat_id.startswith("-"):
            tgid = TelegramID(-int(chat_id))
            peer_type = "chat"
        else:
            return self.get_error_response(400, "tgid_invalid", "Invalid Telegram chat ID.")

        user, err = await self.get_user(request.query.get("user_id", None), expect_logged_in=None,
                                        require_puppeting=False)
        if err is not None:
            return err
        elif user and not await user_has_power_level(room_id, self.az.intent, user, "bridge"):
            return self.get_error_response(403, "not_enough_permissions",
                                           "You do not have the permissions to bridge that room.")

        is_logged_in = user is not None and await user.is_logged_in()
        acting_user = user if is_logged_in else self.context.bot
        if not acting_user:
            return self.get_login_response(status=403, errcode="not_logged_in",
                                           error="You are not logged in and there is no relay bot.")

        portal = Portal.get_by_tgid(tgid, peer_type=peer_type)
        if portal.mxid == room_id:
            return self.get_error_response(200, "bridge_exists",
                                           "Telegram chat is already bridged to that Matrix room.")
        elif portal.mxid:
            force = request.query.get("force", None)
            if force in ("delete", "unbridge"):
                delete = force == "delete"
                await portal.cleanup_portal("Portal deleted (moving to another room)" if delete
                                            else "Room unbridged (portal moving to another room)",
                                            puppets_only=not delete)
            else:
                return self.get_error_response(409, "chat_already_bridged",
                                               "Telegram chat is already bridged to another "
                                               "Matrix room.")

        async with portal._room_create_lock:
            entity: Optional[TypeChat] = None
            try:
                entity = await acting_user.client.get_entity(portal.peer)
            except Exception:
                self.log.exception("Failed to get_entity(%s) for manual bridging.", portal.peer)

            if not entity or isinstance(entity, (ChatForbidden, ChannelForbidden)):
                if is_logged_in:
                    return self.get_error_response(403, "user_not_in_chat",
                                                   "Failed to get info of Telegram chat. "
                                                   "Are you in the chat?")
                return self.get_error_response(403, "bot_not_in_chat",
                                               "Failed to get info of Telegram chat. "
                                               "Is the relay bot in the chat?")

            portal.mxid = room_id
            portal.by_mxid[portal.mxid] = portal
            (portal.title, portal.about, levels,
             portal.encrypted) = await get_initial_state(self.az.intent, room_id)
            portal.photo_id = ""
            await portal.save()

        asyncio.ensure_future(portal.update_matrix_room(user, entity, direct=False, levels=levels),
                              loop=self.loop)

        return web.Response(status=202, body="{}")

    async def create_chat(self, request: web.Request) -> web.Response:
        err = self.check_authorization(request)
        if err is not None:
            return err

        data = await self.get_data(request)
        if not data:
            return self.get_error_response(400, "json_invalid", "Invalid JSON.")

        room_id = request.match_info["mxid"]
        if Portal.get_by_mxid(room_id):
            return self.get_error_response(409, "room_already_bridged",
                                           "Room is already bridged to another Telegram chat.")

        user, err = await self.get_user(request.query.get("user_id", None), expect_logged_in=None,
                                        require_puppeting=False)
        if err is not None:
            return err
        elif not await user.is_logged_in() or user.is_bot:
            return self.get_error_response(403, "not_logged_in_real_account",
                                           "You are not logged in with a real account.")
        elif not await user_has_power_level(room_id, self.az.intent, user, "bridge"):
            return self.get_error_response(403, "not_enough_permissions",
                                           "You do not have the permissions to bridge that room.")

        try:
            title, about, _, encrypted = await get_initial_state(self.az.intent, room_id)
        except (MatrixRequestError, IntentError):
            return self.get_error_response(403, "bot_not_in_room",
                                           "The bridge bot is not in the given room.")

        about = data.get("about", about)

        title = data.get("title", title)
        if len(title) == 0:
            return self.get_error_response(400, "body_value_invalid", "Title can not be empty.")

        type = data.get("type", "")
        if type not in ("group", "chat", "supergroup", "channel"):
            return self.get_error_response(400, "body_value_invalid",
                                           "Given chat type is not valid.")

        supergroup = type == "supergroup"
        type = {
            "supergroup": "channel",
            "channel": "channel",
            "chat": "chat",
            "group": "chat",
        }[type]

        portal = Portal(tgid=TelegramID(0), mxid=room_id, title=title, about=about, peer_type=type,
                        encrypted=encrypted)
        try:
            await portal.create_telegram_chat(user, supergroup=supergroup)
        except ValueError as e:
            await portal.delete()
            return self.get_error_response(500, "unknown_error", e.args[0])

        return web.json_response({
            "chat_id": portal.tgid,
        }, status=201)

    async def disconnect_chat(self, request: web.Request) -> web.Response:
        err = self.check_authorization(request)
        if err is not None:
            return err

        portal = Portal.get_by_mxid(request.match_info["mxid"])
        if not portal or not portal.tgid:
            return self.get_error_response(404, "portal_not_found",
                                           "Room is not a portal.")

        user, err = await self.get_user(request.query.get("user_id", None), expect_logged_in=None,
                                        require_puppeting=False, require_user=False)
        if err is not None:
            return err
        elif user and not await user_has_power_level(portal.mxid, self.az.intent, user,
                                                     "unbridge"):
            return self.get_error_response(403, "not_enough_permissions",
                                           "You do not have the permissions to unbridge that room.")

        delete = request.query.get("delete", "").lower() in ("true", "t", "1", "yes", "y")
        sync = request.query.get("delete", "").lower() in ("true", "t", "1", "yes", "y")

        coro = portal.cleanup_and_delete() if delete else portal.unbridge()
        if sync:
            try:
                await coro
            except Exception:
                self.log.exception("Failed to disconnect chat")
                return self.get_error_response(500, "exception", "Failed to disconnect chat")
        else:
            asyncio.ensure_future(coro, loop=self.loop)
        return web.json_response({}, status=200 if sync else 202)

    async def get_user_info(self, request: web.Request) -> web.Response:
        data, user, err = await self.get_user_request_info(request, expect_logged_in=None,
                                                           require_puppeting=False)
        if err is not None:
            return err

        user_data = None
        if await user.is_logged_in():
            me = await user.client.get_me()
            await user.update_info(me)
            user_data = {
                "id": user.tgid,
                "username": user.username,
                "first_name": me.first_name,
                "last_name": me.last_name,
                "phone": me.phone,
                "is_bot": user.is_bot,
            }
        return web.json_response({
            "telegram": user_data,
            "mxid": user.mxid,
            "permissions": user.permissions,
        })

    async def get_chats(self, request: web.Request) -> web.Response:
        data, user, err = await self.get_user_request_info(request, expect_logged_in=True)
        if err is not None:
            return err

        if not user.is_bot:
            return web.json_response([{
                "id": chat.id,
                "title": chat.title,
            } async for chat in user.client.iter_dialogs(ignore_migrated=True, archived=False)])
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

    async def logout(self, request: web.Request) -> web.Response:
        _, user, err = await self.get_user_request_info(request, expect_logged_in=True,
                                                        require_puppeting=False,
                                                        want_data=False)
        if err is not None:
            return err
        await user.log_out()
        return web.json_response({}, status=200)

    async def bridge_info(self, request: web.Request) -> web.Response:
        return web.json_response({
            "relaybot_username": (self.context.bot.username
                                  if self.context.bot is not None else None),
        }, status=200)

    @staticmethod
    async def error_middleware(_, handler: Callable[[web.Request], Awaitable[web.Response]]
                               ) -> Callable[[web.Request], Awaitable[web.Response]]:
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

    def get_mx_login_response(self, status=200, state="", username="", phone="", human_tg_id="",
                              mxid="", message="", error="", errcode=""):
        raise NotImplementedError()

    def get_login_response(self, status=200, state="", username="", phone: str = "",
                           human_tg_id: str = "", mxid="", message="", error="", errcode=""
                           ) -> web.Response:
        if username or phone:
            resp = {
                "state": "logged-in",
                "username": username,
                "phone": phone,
            }
        elif message:
            resp = {
                "state": state,
                "message": message,
            }
        else:
            resp = {
                "error": error,
                "errcode": errcode,
            }
            if state:
                resp["state"] = state
        return web.json_response(resp, status=status)

    def check_authorization(self, request: web.Request) -> Optional[web.Response]:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {self.secret}":
            return self.get_error_response(error="Shared secret is not valid.",
                                           errcode="shared_secret_invalid",
                                           status=401)
        return None

    @staticmethod
    async def get_data(request: web.Request) -> Optional[dict]:
        try:
            return await request.json()
        except json.JSONDecodeError:
            return None

    async def get_user(self, mxid: Optional[UserID], expect_logged_in: Optional[bool] = False,
                       require_puppeting: bool = True, require_user: bool = True
                       ) -> Tuple[Optional[User], Optional[web.Response]]:
        if not mxid:
            if not require_user:
                return None, None
            return None, self.get_login_response(error="User ID not given.",
                                                 errcode="mxid_empty", status=400)

        user = await User.get_by_mxid(mxid).ensure_started(even_if_no_session=True)
        if require_puppeting and not user.puppet_whitelisted:
            return user, self.get_login_response(error="You are not whitelisted.",
                                                 errcode="mxid_not_whitelisted", status=403)
        if expect_logged_in is not None:
            logged_in = await user.is_logged_in()
            if not expect_logged_in and logged_in:
                return user, self.get_login_response(username=user.username, phone=user.phone,
                                                     status=409,
                                                     error="You are already logged in.",
                                                     errcode="already_logged_in")
            elif expect_logged_in and not logged_in:
                return user, self.get_login_response(status=403, error="You are not logged in.",
                                                     errcode="not_logged_in")
        return user, None

    async def get_user_request_info(self, request: web.Request,
                                    expect_logged_in: Optional[bool] = False,
                                    require_puppeting: bool = False,
                                    want_data: bool = True,
                                    ) -> (Tuple[Optional[Dict], Optional[User],
                                                Optional[web.Response]]):
        err = self.check_authorization(request)
        if err is not None:
            return err

        data = None
        if want_data and (request.method == "POST" or request.method == "PUT"):
            data = await self.get_data(request)
            if not data:
                return None, None, self.get_login_response(error="Invalid JSON.",
                                                           errcode="json_invalid", status=400)

        mxid = request.match_info["mxid"]
        user, err = await self.get_user(mxid, expect_logged_in, require_puppeting)

        return data, user, err
