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
from __future__ import annotations

from typing import TYPE_CHECKING, Awaitable, Callable
import asyncio
import datetime
import json
import logging

from aiohttp import web
from telethon.errors import SessionPasswordNeededError
from telethon.tl.custom import QRLogin
from telethon.tl.functions.messages import GetAllStickersRequest
from telethon.tl.types import ChannelForbidden, ChatForbidden, TypeChat, User as TLUser
from telethon.utils import get_peer_id, resolve_id

from mautrix.appservice import AppService
from mautrix.client import Client
from mautrix.errors import IntentError, MatrixRequestError
from mautrix.types import UserID
from mautrix.util import background_task

from ...commands.portal.util import get_initial_state, user_has_power_level
from ...portal import Portal
from ...types import TelegramID
from ...user import User
from ..common import AuthAPI

if TYPE_CHECKING:
    from ...__main__ import TelegramBridge


class ProvisioningAPI(AuthAPI):
    log: logging.Logger = logging.getLogger("mau.web.provisioning")
    secret: str
    az: AppService
    bridge: "TelegramBridge"
    app: web.Application

    def __init__(self, bridge: "TelegramBridge") -> None:
        super().__init__(bridge.loop)
        self.secret = bridge.config["appservice.provisioning.shared_secret"]
        self.az = bridge.az
        self.bridge = bridge

        self.app = web.Application(loop=bridge.loop, middlewares=[self.error_middleware])

        portal_prefix = "/v1/portal/{mxid}"
        self.app.router.add_route("GET", f"{portal_prefix}", self.get_portal_by_mxid)
        self.app.router.add_route("GET", "/v1/portal/{tgid:-[0-9]+}", self.get_portal_by_tgid)
        self.app.router.add_route(
            "POST", portal_prefix + "/connect/{chat_id:-[0-9]+}", self.connect_chat
        )
        self.app.router.add_route("POST", f"{portal_prefix}/create", self.create_chat)
        self.app.router.add_route("POST", f"{portal_prefix}/disconnect", self.disconnect_chat)

        user_prefix = "/v1/user/{mxid}"
        self.app.router.add_route("GET", f"{user_prefix}", self.get_user_info)
        self.app.router.add_route("GET", f"{user_prefix}/chats", self.get_chats)
        self.app.router.add_route("GET", f"{user_prefix}/contacts", self.get_contacts)
        self.app.router.add_route(
            "GET", f"{user_prefix}/resolve_identifier/{{identifier}}", self.resolve_identifier
        )
        self.app.router.add_route("POST", f"{user_prefix}/pm/{{identifier}}", self.start_dm)

        self.app.router.add_route("GET", f"{user_prefix}/stickersets", self.get_stickersets)

        self.app.router.add_route("POST", f"{user_prefix}/retry_takeout", self.retry_takeout)

        self.app.router.add_route("POST", f"{user_prefix}/logout", self.logout)
        self.app.router.add_route("GET", f"{user_prefix}/login/qr", self.login_qr)
        self.app.router.add_route("POST", f"{user_prefix}/login/bot_token", self.send_bot_token)
        self.app.router.add_route("POST", f"{user_prefix}/login/request_code", self.request_code)
        self.app.router.add_route("POST", f"{user_prefix}/login/send_code", self.send_code)
        self.app.router.add_route("POST", f"{user_prefix}/login/send_password", self.send_password)

        self.app.router.add_route("GET", "/v1/bridge", self.bridge_info)

    async def get_portal_by_mxid(self, request: web.Request) -> web.Response:
        err = self.check_authorization(request)
        if err is not None:
            return err

        mxid = request.match_info["mxid"]
        portal = await Portal.get_by_mxid(mxid)
        if not portal:
            return self.get_error_response(
                404, "portal_not_found", "Portal with given Matrix ID not found."
            )
        return await self._get_portal_response(UserID(request.query.get("user_id", "")), portal)

    async def get_portal_by_tgid(self, request: web.Request) -> web.Response:
        err = self.check_authorization(request)
        if err is not None:
            return err

        try:
            tgid, _ = resolve_id(int(request.match_info["tgid"]))
        except ValueError:
            return self.get_error_response(400, "tgid_invalid", "Given chat ID is not valid.")
        portal = await Portal.get_by_tgid(tgid)
        if not portal:
            return self.get_error_response(
                404, "portal_not_found", "Portal to given Telegram chat not found."
            )
        return await self._get_portal_response(UserID(request.query.get("user_id", "")), portal)

    async def _get_portal_response(self, user_id: UserID, portal: Portal) -> web.Response:
        user, _ = await self.get_user(user_id, expect_logged_in=None, require_puppeting=False)
        return web.json_response(
            {
                "mxid": portal.mxid,
                "chat_id": get_peer_id(portal.peer),
                "peer_type": portal.peer_type,
                "title": portal.title,
                "about": portal.about,
                "username": portal.username,
                "megagroup": portal.megagroup,
                "can_unbridge": (await portal.can_user_perform(user, "unbridge"))
                if user
                else False,
            }
        )

    async def connect_chat(self, request: web.Request) -> web.Response:
        err = self.check_authorization(request)
        if err is not None:
            return err

        room_id = request.match_info["mxid"]
        if await Portal.get_by_mxid(room_id):
            return self.get_error_response(
                409, "room_already_bridged", "Room is already bridged to another Telegram chat."
            )

        chat_id = request.match_info["chat_id"]
        if chat_id.startswith("-100"):
            tgid = TelegramID(int(chat_id[4:]))
            peer_type = "channel"
        elif chat_id.startswith("-"):
            tgid = TelegramID(-int(chat_id))
            peer_type = "chat"
        else:
            return self.get_error_response(400, "tgid_invalid", "Invalid Telegram chat ID.")

        user, err = await self.get_user(
            request.query.get("user_id", None), expect_logged_in=None, require_puppeting=False
        )
        if err is not None:
            return err
        elif user and not await user_has_power_level(room_id, self.az.intent, user, "bridge"):
            return self.get_error_response(
                403,
                "not_enough_permissions",
                "You do not have the permissions to bridge that room.",
            )

        is_logged_in = user is not None and await user.is_logged_in()
        acting_user = user if is_logged_in else self.bridge.bot
        if not acting_user:
            return self.get_login_response(
                status=403,
                errcode="not_logged_in",
                error="You are not logged in and there is no relay bot.",
            )

        portal = await Portal.get_by_tgid(tgid, peer_type=peer_type)
        if portal.mxid == room_id:
            return self.get_error_response(
                200, "bridge_exists", "Telegram chat is already bridged to that Matrix room."
            )
        elif portal.mxid:
            force = request.query.get("force", None)
            if force in ("delete", "unbridge"):
                delete = force == "delete"
                await portal.cleanup_portal(
                    "Portal deleted (moving to another room)"
                    if delete
                    else "Room unbridged (portal moving to another room)",
                    puppets_only=not delete,
                )
            else:
                return self.get_error_response(
                    409,
                    "chat_already_bridged",
                    "Telegram chat is already bridged to another Matrix room.",
                )

        async with portal._room_create_lock:
            entity: TypeChat | None = None
            try:
                entity = await acting_user.client.get_entity(portal.peer)
            except Exception:
                self.log.exception("Failed to get_entity(%s) for manual bridging.", portal.peer)

            if not entity or isinstance(entity, (ChatForbidden, ChannelForbidden)):
                if is_logged_in:
                    return self.get_error_response(
                        403,
                        "user_not_in_chat",
                        "Failed to get info of Telegram chat. Are you in the chat?",
                    )
                return self.get_error_response(
                    403,
                    "bot_not_in_chat",
                    "Failed to get info of Telegram chat. Is the relay bot in the chat?",
                )

            portal.mxid = room_id
            portal.by_mxid[portal.mxid] = portal
            (portal.title, portal.about, levels, portal.encrypted) = await get_initial_state(
                self.az.intent, room_id
            )
            portal.photo_id = ""
            await portal.save()

        background_task.create(portal.update_matrix_room(user, entity, levels=levels))

        return web.Response(status=202, body="{}")

    async def create_chat(self, request: web.Request) -> web.Response:
        err = self.check_authorization(request)
        if err is not None:
            return err

        data = await self.get_data(request)
        if not data:
            return self.get_error_response(400, "json_invalid", "Invalid JSON.")

        room_id = request.match_info["mxid"]
        if await Portal.get_by_mxid(room_id):
            return self.get_error_response(
                409, "room_already_bridged", "Room is already bridged to another Telegram chat."
            )

        user, err = await self.get_user(
            request.query.get("user_id", None), expect_logged_in=None, require_puppeting=False
        )
        if err is not None:
            return err
        elif not await user.is_logged_in() or user.is_bot:
            return self.get_error_response(
                403, "not_logged_in_real_account", "You are not logged in with a real account."
            )
        elif not await user_has_power_level(room_id, self.az.intent, user, "bridge"):
            return self.get_error_response(
                403,
                "not_enough_permissions",
                "You do not have the permissions to bridge that room.",
            )

        try:
            title, about, _, encrypted = await get_initial_state(self.az.intent, room_id)
        except (MatrixRequestError, IntentError):
            return self.get_error_response(
                403, "bot_not_in_room", "The bridge bot is not in the given room."
            )

        about = data.get("about", about)

        title = data.get("title", title)
        if len(title) == 0:
            return self.get_error_response(400, "body_value_invalid", "Title can not be empty.")

        type = data.get("type", "")
        if type not in ("group", "chat", "supergroup", "channel"):
            return self.get_error_response(
                400, "body_value_invalid", "Given chat type is not valid."
            )

        supergroup = type == "supergroup"
        type = {
            "supergroup": "channel",
            "channel": "channel",
            "chat": "chat",
            "group": "chat",
        }[type]

        portal = Portal(
            tgid=TelegramID(0),
            mxid=room_id,
            title=title,
            about=about,
            peer_type=type,
            encrypted=encrypted,
            tg_receiver=TelegramID(0),
        )
        try:
            await portal.create_telegram_chat(user, supergroup=supergroup)
        except ValueError as e:
            await portal.delete()
            return self.get_error_response(500, "unknown_error", e.args[0])

        return web.json_response(
            {
                "chat_id": portal.tgid,
            },
            status=201,
        )

    async def disconnect_chat(self, request: web.Request) -> web.Response:
        err = self.check_authorization(request)
        if err is not None:
            return err

        portal = await Portal.get_by_mxid(request.match_info["mxid"])
        if not portal or not portal.tgid:
            return self.get_error_response(404, "portal_not_found", "Room is not a portal.")

        user, err = await self.get_user(
            request.query.get("user_id", None),
            expect_logged_in=None,
            require_puppeting=False,
            require_user=False,
        )
        if err is not None:
            return err
        elif user and not await user_has_power_level(
            portal.mxid, self.az.intent, user, "unbridge"
        ):
            return self.get_error_response(
                403,
                "not_enough_permissions",
                "You do not have the permissions to unbridge that room.",
            )

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
            background_task.create(coro)
        return web.json_response({}, status=200 if sync else 202)

    async def get_user_info(self, request: web.Request) -> web.Response:
        data, user, err = await self.get_user_request_info(
            request, expect_logged_in=None, require_puppeting=False
        )
        if err is not None:
            return err

        user_data = None
        if await user.is_logged_in():
            me = await user.get_me()
            if me:
                await user.update_info(me)
                user_data = {
                    "id": user.tgid,
                    "username": user.tg_username,
                    "first_name": me.first_name,
                    "last_name": me.last_name,
                    "phone": user.tg_phone,
                    "is_bot": user.is_bot,
                }
        return web.json_response(
            {
                "telegram": user_data,
                "mxid": user.mxid,
                "permissions": user.permissions,
            }
        )

    async def get_chats(self, request: web.Request) -> web.Response:
        data, user, err = await self.get_user_request_info(request, expect_logged_in=True)
        if err is not None:
            return err

        if not user.is_bot:
            return web.json_response(
                [
                    {
                        "id": chat.id,
                        "title": chat.title,
                    }
                    async for chat in user.client.iter_dialogs(
                        ignore_migrated=True, archived=False
                    )
                ]
            )
        else:
            return web.json_response(
                [
                    {
                        "id": get_peer_id(chat.peer),
                        "title": chat.title,
                    }
                    for chat in (await user.get_cached_portals()).values()
                    if chat.tgid
                ]
            )

    async def get_contacts(self, request: web.Request) -> web.Response:
        data, user, err = await self.get_user_request_info(request, expect_logged_in=True)
        if err is not None:
            return err
        return web.json_response(data=await user.sync_contacts())

    async def _resolve_id(
        self, request: web.Request
    ) -> tuple[Portal | None, User | None, TLUser | None, web.Response | None]:
        data, user, err = await self.get_user_request_info(request, expect_logged_in=True)
        if err is not None:
            return None, user, None, err
        try:
            identifier: str | int = request.match_info["identifier"]
            if isinstance(identifier, str) and identifier.isdecimal():
                identifier = int(identifier)
            target = await user.client.get_entity(identifier)
        except ValueError:
            return (
                None,
                user,
                None,
                web.json_response(
                    {
                        "error": "Invalid user identifier or user not found.",
                        "errcode": "M_NOT_FOUND",
                    },
                    status=404,
                ),
            )

        if not target:
            return (
                None,
                user,
                None,
                web.json_response(
                    {
                        "error": "User not found.",
                        "errcode": "M_NOT_FOUND",
                    },
                    status=404,
                ),
            )
        elif not isinstance(target, TLUser):
            return (
                None,
                user,
                None,
                web.json_response(
                    {
                        "error": "Identifier is not a user.",
                        "errcode": "FI.MAU.TELEGRAM_ID_NOT_USER",
                    },
                    status=400,
                ),
            )
        portal = await Portal.get_by_entity(target, tg_receiver=user.tgid)
        return portal, user, target, None

    async def resolve_identifier(self, request: web.Request) -> web.Response:
        portal, user, target, err = await self._resolve_id(request)
        if err is not None:
            return err
        puppet = await portal.get_dm_puppet()
        await puppet.update_info(user, target)
        return web.json_response(
            {
                "room_id": portal.mxid,
                "just_created": False,
                "id": portal.tgid,
                "contact_info": puppet.contact_info,
            },
            status=200,
        )

    async def start_dm(self, request: web.Request) -> web.Response:
        portal, user, target, err = await self._resolve_id(request)
        if err is not None:
            return err
        puppet = await portal.get_dm_puppet()
        if portal.mxid:
            just_created = False
        else:
            await portal.create_matrix_room(user, target, [user.mxid])
            just_created = True
        return web.json_response(
            {
                "room_id": portal.mxid,
                "just_created": just_created,
                "id": portal.tgid,
                "contact_info": puppet.contact_info,
            },
            status=201 if just_created else 200,
        )

    async def get_stickersets(self, request: web.Request) -> web.Response:
        _, user, err = await self.get_user_request_info(
            request, expect_logged_in=True, want_data=False
        )
        if err is not None:
            return err
        result = await user.client(GetAllStickersRequest(0))
        resp = []
        for stickerset in result.sets:
            resp.append(stickerset.short_name)
        return web.json_response(resp, status=200)

    async def retry_takeout(self, request: web.Request) -> web.Response:
        data, user, err = await self.get_user_request_info(
            request, expect_logged_in=True, want_data=False
        )
        if err is not None:
            return err
        if not user.takeout_requested:
            return web.json_response(
                {
                    "error": "There was no takeout requested",
                },
                status=400,
            )
        user.takeout_retry_immediate.set()
        return web.json_response({}, status=200)

    async def login_qr(self, request: web.Request) -> web.Response:
        _, user, err = await self.get_user_request_info(request, websocket=True)
        if err is not None:
            return err

        await user.ensure_started(even_if_no_session=True)
        qr_login = QRLogin(user.client, ignored_ids=[])

        ws = web.WebSocketResponse(protocols=["net.maunium.telegram.login"])
        await ws.prepare(request)

        retries = 0
        user_info = None
        while retries < 4:
            try:
                await qr_login.recreate()
                await ws.send_json(
                    {
                        "code": qr_login.url,
                        "timeout": int(
                            (
                                qr_login.expires - datetime.datetime.now(tz=datetime.timezone.utc)
                            ).total_seconds()
                        ),
                    }
                )
                user_info = await qr_login.wait()
                break
            except asyncio.TimeoutError:
                retries += 1
            except SessionPasswordNeededError:
                await ws.send_json({"success": False, "error": "password-needed"})
                await ws.close()
                return ws
        else:
            await ws.send_json({"success": False, "error": "timeout"})
            await ws.close()
            return ws

        await self.postprocess_login(user, user_info)
        await ws.send_json({"success": True})
        await ws.close()
        return ws

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
        _, user, err = await self.get_user_request_info(
            request, expect_logged_in=None, require_puppeting=False, want_data=False
        )
        if err is not None:
            return err
        await user.log_out()
        return web.json_response({}, status=200)

    async def bridge_info(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "relaybot_username": (
                    self.bridge.bot.tg_username if self.bridge.bot is not None else None
                ),
            },
            status=200,
        )

    @staticmethod
    async def error_middleware(
        _, handler: Callable[[web.Request], Awaitable[web.Response]]
    ) -> Callable[[web.Request], Awaitable[web.Response]]:
        async def middleware_handler(request: web.Request) -> web.Response:
            try:
                return await handler(request)
            except web.HTTPException as ex:
                return web.json_response(
                    {
                        "error": f"Unhandled HTTP {ex.status}",
                        "errcode": f"unhandled_http_{ex.status}",
                    },
                    status=ex.status,
                )

        return middleware_handler

    @staticmethod
    def get_error_response(status=200, errcode="", error="") -> web.Response:
        return web.json_response(
            {
                "error": error,
                "errcode": errcode,
            },
            status=status,
        )

    def get_mx_login_response(
        self,
        status=200,
        state="",
        username="",
        phone="",
        human_tg_id="",
        mxid="",
        message="",
        error="",
        errcode="",
    ):
        raise NotImplementedError()

    def get_login_response(
        self,
        status=200,
        state="",
        username="",
        phone: str = "",
        human_tg_id: str = "",
        mxid="",
        message="",
        error="",
        errcode="",
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

    def check_authorization(self, request: web.Request) -> web.Response | None:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {self.secret}":
            return self.get_error_response(
                error="Shared secret is not valid.", errcode="shared_secret_invalid", status=401
            )
        return None

    def check_websocket_authorization(self, request: web.Request) -> web.Response | None:
        auth_parts = request.headers.get("Sec-WebSocket-Protocol").split(",")
        for part in auth_parts:
            if part.strip() == f"net.maunium.telegram.auth-{self.secret}":
                return None
        return self.get_error_response(
            error="Shared secret is not valid.", errcode="shared_secret_invalid", status=401
        )

    @staticmethod
    async def get_data(request: web.Request) -> dict | None:
        try:
            return await request.json()
        except json.JSONDecodeError:
            return None

    async def get_user(
        self,
        mxid: UserID | None,
        expect_logged_in: bool | None = False,
        require_puppeting: bool = True,
        require_user: bool = True,
    ) -> tuple[User | None, web.Response | None]:
        if not mxid:
            if not require_user:
                return None, None
            return None, self.get_login_response(
                error="User ID not given.", errcode="mxid_empty", status=400
            )
        try:
            Client.parse_user_id(mxid)
        except ValueError:
            return None, self.get_login_response(
                error="Invalid user ID", errcode="mxid_invalid", status=400
            )

        user = await User.get_and_start_by_mxid(mxid, even_if_no_session=True)
        if require_puppeting and not user.puppet_whitelisted:
            return user, self.get_login_response(
                error="You are not whitelisted.", errcode="mxid_not_whitelisted", status=403
            )
        if expect_logged_in is not None:
            logged_in = await user.is_logged_in()
            if not expect_logged_in and logged_in:
                return user, self.get_login_response(
                    username=user.tg_username,
                    phone=user.tg_phone,
                    status=409,
                    error="You are already logged in.",
                    errcode="already_logged_in",
                )
            elif expect_logged_in and not logged_in:
                return user, self.get_login_response(
                    status=403, error="You are not logged in.", errcode="not_logged_in"
                )
        return user, None

    async def get_user_request_info(
        self,
        request: web.Request,
        expect_logged_in: bool | None = False,
        require_puppeting: bool = False,
        want_data: bool = True,
        websocket: bool = False,
    ) -> tuple[dict | None, User | None, web.Response | None]:
        if not websocket:
            err = self.check_authorization(request)
        else:
            err = self.check_websocket_authorization(request)
        if err is not None:
            return None, None, err

        data = None
        if want_data and (request.method == "POST" or request.method == "PUT"):
            data = await self.get_data(request)
            if data is None:
                return (
                    None,
                    None,
                    self.get_login_response(
                        error="Invalid JSON.", errcode="json_invalid", status=400
                    ),
                )

        mxid = request.match_info["mxid"]
        user, err = await self.get_user(mxid, expect_logged_in, require_puppeting)

        return data, user, err
