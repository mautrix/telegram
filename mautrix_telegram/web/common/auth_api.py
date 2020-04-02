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
from abc import abstractmethod
import abc
import asyncio
import logging

from aiohttp import web

from telethon.errors import *

from mautrix.bridge import OnlyLoginSelf, InvalidAccessToken

from ...commands.telegram.auth import enter_password
from ...util import format_duration
from ...puppet import Puppet
from ...user import User


class AuthAPI(abc.ABC):
    log: logging.Logger = logging.getLogger("mau.web.auth")
    loop: asyncio.AbstractEventLoop

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop

    @abstractmethod
    def get_login_response(self, status: int = 200, state: str = "", username: str = "",
                           phone: str = "", human_tg_id: str = "", mxid: str = "",
                           message: str = "", error: str = "", errcode: str = "") -> web.Response:
        raise NotImplementedError()

    @abstractmethod
    def get_mx_login_response(self, status: int = 200, state: str = "", username: str = "",
                              phone: str = "", human_tg_id: str = "", mxid: str = "",
                              message: str = "", error: str = "", errcode: str = ""
                              ) -> web.Response:
        raise NotImplementedError()

    async def post_matrix_token(self, user: User, token: str) -> web.Response:
        puppet = Puppet.get(user.tgid)
        if puppet.is_real_user:
            return self.get_mx_login_response(state="already-logged-in", status=409,
                                              error="You have already logged in with your Matrix "
                                                    "account.", errcode="already-logged-in")

        try:
            await puppet.switch_mxid(token.strip(), user.mxid)
        except OnlyLoginSelf:
            return self.get_mx_login_response(status=403, errcode="only-login-self",
                                              error="You can only log in as your own Matrix user.")
        except InvalidAccessToken:
            return self.get_mx_login_response(status=401, errcode="invalid-access-token",
                                              error="Failed to verify access token.")
        return self.get_mx_login_response(mxid=user.mxid, status=200, state="logged-in")

    async def post_matrix_password(self, user: User, password: str) -> web.Response:
        return self.get_mx_login_response(mxid=user.mxid, status=501, error="Not yet implemented",
                                          errcode="not-yet-implemented")

    async def post_login_phone(self, user: User, phone: str) -> web.Response:
        if not phone or not phone.strip():
            return self.get_login_response(mxid=user.mxid, state="request", status=400,
                                           errcode="phone_number_invalid",
                                           error="Phone number not given.")
        try:
            await user.client.sign_in(phone.strip())
            return self.get_login_response(mxid=user.mxid, state="code", status=200,
                                           message="Code requested successfully. Check your SMS "
                                                   "or Telegram client and enter the code below.")
        except PhoneNumberInvalidError:
            return self.get_login_response(mxid=user.mxid, state="request", status=400,
                                           errcode="phone_number_invalid",
                                           error="Invalid phone number.")
        except PhoneNumberBannedError:
            return self.get_login_response(mxid=user.mxid, state="request", status=403,
                                           errcode="phone_number_banned",
                                           error="Your phone number is banned from Telegram.")
        except PhoneNumberAppSignupForbiddenError:
            return self.get_login_response(mxid=user.mxid, state="request", status=403,
                                           errcode="phone_number_app_signup_forbidden",
                                           error="You have disabled 3rd party apps on your "
                                                 "account.")
        except PhoneNumberUnoccupiedError:
            return self.get_login_response(mxid=user.mxid, state="request", status=404,
                                           errcode="phone_number_unoccupied",
                                           error="That phone number has not been registered.")
        except PhoneNumberFloodError:
            return self.get_login_response(
                mxid=user.mxid, state="request", status=429, errcode="phone_number_flood",
                error="Your phone number has been temporarily blocked for flooding. "
                      "The ban is usually applied for around a day.")
        except FloodWaitError as e:
            return self.get_login_response(
                mxid=user.mxid, state="request", status=429, errcode="flood_wait",
                error="Your phone number has been temporarily blocked for flooding. "
                      f"Please wait for {format_duration(e.seconds)} before trying again.")
        except Exception:
            self.log.exception("Error requesting phone code")
            return self.get_login_response(mxid=user.mxid, state="request", status=500,
                                           errcode="unknown_error",
                                           error="Internal server error while requesting code.")

    async def postprocess_login(self, user: User, user_info) -> None:
        existing_user = User.get_by_tgid(user_info.id)
        if existing_user and existing_user != user:
            await existing_user.log_out()
        asyncio.ensure_future(user.post_login(user_info, first_login=True), loop=self.loop)
        if user.command_status and user.command_status["action"] == "Login":
            user.command_status = None

    async def post_login_token(self, user: User, token: str) -> web.Response:
        try:
            user_info = await user.client.sign_in(bot_token=token.strip())
            await self.postprocess_login(user, user_info)
            return self.get_login_response(mxid=user.mxid, state="logged-in", status=200,
                                           username=user_info.username, phone=None,
                                           human_tg_id=f"@{user_info.username}")
        except AccessTokenInvalidError:
            return self.get_login_response(mxid=user.mxid, state="token", status=401,
                                           errcode="bot_token_invalid",
                                           error="Bot token invalid.")
        except AccessTokenExpiredError:
            return self.get_login_response(mxid=user.mxid, state="token", status=403,
                                           errcode="bot_token_expired",
                                           error="Bot token expired.")
        except Exception:
            self.log.exception("Error sending bot token")
            return self.get_login_response(mxid=user.mxid, state="token", status=500,
                                           error="Internal server error while sending token.")

    async def post_login_code(self, user: User, code: int, password_in_data: bool
                              ) -> Optional[web.Response]:
        try:
            user_info = await user.client.sign_in(code=code)
            await self.postprocess_login(user, user_info)
            human_tg_id = f"@{user_info.username}" if user_info.username else f"+{user_info.phone}"
            return self.get_login_response(mxid=user.mxid, state="logged-in", status=200,
                                           username=user_info.username, phone=user_info.phone,
                                           human_tg_id=human_tg_id)
        except PhoneCodeInvalidError:
            return self.get_login_response(mxid=user.mxid, state="code", status=401,
                                           errcode="phone_code_invalid",
                                           error="Incorrect phone code.")
        except PhoneCodeExpiredError:
            return self.get_login_response(mxid=user.mxid, state="code", status=403,
                                           errcode="phone_code_expired",
                                           error="Phone code expired.")
        except SessionPasswordNeededError:
            if not password_in_data:
                if user.command_status and user.command_status["action"] == "Login":
                    user.command_status = {
                        "next": enter_password,
                        "action": "Login (password entry)",
                    }
                message = (
                    "Code accepted, but you have 2-factor "
                    "authentication enabled. Please enter your password."
                )
                return self.get_login_response(
                    mxid=user.mxid, state="password", status=202, message=message
                )
            return None
        except Exception:
            self.log.exception("Error sending phone code")
            return self.get_login_response(mxid=user.mxid, state="code", status=500,
                                           errcode="unknown_error",
                                           error="Internal server error while sending code.")

    async def post_login_password(self, user: User, password: str) -> web.Response:
        try:
            user_info = await user.client.sign_in(password=password.strip())
            await self.postprocess_login(user, user_info)
            human_tg_id = f"@{user_info.username}" if user_info.username else f"+{user_info.phone}"
            return self.get_login_response(mxid=user.mxid, state="logged-in", status=200,
                                           username=user_info.username, phone=user_info.phone,
                                           human_tg_id=human_tg_id)
        except PasswordEmptyError:
            return self.get_login_response(mxid=user.mxid, state="password", status=400,
                                           errcode="password_empty",
                                           error="Empty password.")
        except PasswordHashInvalidError:
            return self.get_login_response(mxid=user.mxid, state="password", status=401,
                                           errcode="password_invalid",
                                           error="Incorrect password.")
        except Exception:
            self.log.exception("Error sending password")
            return self.get_login_response(mxid=user.mxid, state="password", status=500,
                                           errcode="unknown_error",
                                           error="Internal server error while sending password.")
