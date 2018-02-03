# -*- coding: future_fstrings -*-
# matrix-appservice-python - A Matrix Application Service framework written in Python.
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
#
# Partly based on github.com/Cadair/python-appservice-framework (MIT license)
import asyncio
import logging
import aiohttp
from aiohttp import web
from functools import partial
from contextlib import contextmanager
from .intent_api import HTTPAPI


class StateStore:
    def __init__(self):
        self.memberships = {}
        self.power_levels = {}

    def _get_membership(self, room, user):
        return self.memberships.get(room, {}).get(user, "left")

    def is_joined(self, room, user):
        return self._get_membership(room, user) == "join"

    def _set_membership(self, room, user, membership):
        if room not in self.memberships:
            self.memberships[room] = {}
        self.memberships[room][user] = membership

    def joined(self, room, user):
        return self._set_membership(room, user, "join")

    def invited(self, room, user):
        return self._set_membership(room, user, "invite")

    def left(self, room, user):
        return self._set_membership(room, user, "left")

    def has_power_level_data(self, room):
        return room in self.power_levels

    def has_power_level(self, room, user, event):
        room_levels = self.power_levels.get(room, {})
        required = room_levels["events"].get(event, 95)
        has = room_levels["users"].get(user, 0)
        return has >= required

    def set_power_level(self, room, user, level):
        if not room in self.power_levels:
            self.power_levels[room] = {
                "users": {},
                "events": {},
            }
        self.power_levels[room]["users"][user] = level

    def set_power_levels(self, room, content):
        if "events" not in content:
            content["events"] = {}
        if "users" not in content:
            content["users"] = {}
        self.power_levels[room] = content


class AppService:
    def __init__(self, server, domain, as_token, hs_token, bot_localpart, loop=None, log=None,
                 query_user=None, query_alias=None):
        self.server = server
        self.domain = domain
        self.as_token = as_token
        self.hs_token = hs_token
        self.bot_mxid = f"@{bot_localpart}:{domain}"
        self.state_store = StateStore()

        self.transactions = []

        self._http_session = None
        self._intent = None

        self.loop = loop or asyncio.get_event_loop()
        self.log = log or logging.getLogger("mautrix_appservice")

        self.query_user = query_user or (lambda user: None)
        self.query_alias = query_alias or (lambda alias: None)

        self.event_handlers = []

        self.app = web.Application(loop=self.loop)
        self.app.router.add_route("PUT", "/transactions/{transaction_id}",
                                  self._http_handle_transaction)
        self.app.router.add_route("GET", "/rooms/{alias}", self._http_query_alias)
        self.app.router.add_route("GET", "/users/{user_id}", self._http_query_user)

    @property
    def http_session(self):
        if self._http_session is None:
            raise AttributeError("the http_session attribute can only be used "
                                 "from within the `AppService.run` context manager")
        else:
            return self._http_session

    @property
    def intent(self):
        if self._intent is None:
            raise AttributeError("the intent attribute can only be used from "
                                 "within the `AppService.run` context manager")
        else:
            return self._intent

    @contextmanager
    def run(self, host="127.0.0.1", port=8080):
        self._http_session = aiohttp.ClientSession(loop=self.loop)
        self._intent = HTTPAPI(base_url=self.server, domain=self.domain, bot_mxid=self.bot_mxid,
                               token=self.as_token, log=self.log,
                               state_store=self.state_store).bot_intent()

        yield partial(aiohttp.web.run_app, self.app, host=host, port=port)

        self._intent = None
        self._http_session.close()
        self._http_session = None

    def _check_token(self, request):
        try:
            token = request.rel_url.query["access_token"]
        except KeyError:
            return False

        if token != self.hs_token:
            return False

        return True

    async def _http_query_user(self, request):
        if not self._check_token(request):
            return web.Response(status=401)

        user_id = request.match_info["userId"]

        try:
            response = self.query_user(user_id)
        except:
            self.log.exception("Exception in user query handler")
            return web.Response(status=500)

        if not response:
            return web.Response(status=404)
        return web.json_response(response)

    async def _http_query_alias(self, request):
        if not self._check_token(request):
            return web.Response(status=401)

        alias = request.match_info["alias"]

        try:
            response = self.query_alias(alias)
        except:
            self.log.exception("Exception in alias query handler")
            return web.Response(status=500)

        if not response:
            return web.Response(status=404)
        return web.json_response(response)

    async def _http_handle_transaction(self, request):
        if not self._check_token(request):
            return web.Response(status=401)

        transaction_id = request.match_info["transaction_id"]
        if transaction_id in self.transactions:
            return web.Response(status=200)

        json = await request.json()

        try:
            events = json["events"]
        except KeyError:
            return web.Response(status=400)

        for event in events:
            self.handle_matrix_event(event)

        self.transactions.append(transaction_id)

        return web.json_response({})

    def handle_matrix_event(self, event):
        for handler in self.event_handlers:
            try:
                handler(event)
            except:
                self.log.exception("Exception in Matrix event handler")

    def matrix_event_handler(self, func):
        self.event_handlers.append(func)
        return func
