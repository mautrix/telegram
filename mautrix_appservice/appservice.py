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
from contextlib import contextmanager
from aiohttp import web
import aiohttp
import asyncio
import logging

from .intent_api import HTTPAPI
from .state_store import StateStore


class AppService:
    def __init__(self, server, domain, as_token, hs_token, bot_localpart, loop=None, log=None,
                 verify_ssl=True, query_user=None, query_alias=None):
        self.server = server
        self.domain = domain
        self.verify_ssl = verify_ssl
        self.as_token = as_token
        self.hs_token = hs_token
        self.bot_mxid = f"@{bot_localpart}:{domain}"
        self.state_store = StateStore(autosave_file="mx-state.json")
        self.state_store.load("mx-state.json")

        self.transactions = []

        self._http_session = None
        self._intent = None

        self.loop = loop or asyncio.get_event_loop()
        self.log = (logging.getLogger(log) if isinstance(log, str)
                    else log or logging.getLogger("mautrix_appservice"))

        async def default_query_handler(_):
            return None

        self.query_user = query_user or default_query_handler
        self.query_alias = query_alias or default_query_handler

        self.event_handlers = []

        self.app = web.Application(loop=self.loop)
        self.app.router.add_route("PUT", "/transactions/{transaction_id}",
                                  self._http_handle_transaction)
        self.app.router.add_route("GET", "/rooms/{alias}", self._http_query_alias)
        self.app.router.add_route("GET", "/users/{user_id}", self._http_query_user)

        self.matrix_event_handler(self.update_state_store)

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
        connector = None
        if self.server.startswith("https://") and not self.verify_ssl:
            connector = aiohttp.TCPConnector(verify_ssl=False)
        self._http_session = aiohttp.ClientSession(loop=self.loop, connector=connector)
        self._intent = HTTPAPI(base_url=self.server, domain=self.domain, bot_mxid=self.bot_mxid,
                               token=self.as_token, log=self.log, state_store=self.state_store,
                               client_session=self._http_session).bot_intent()

        yield self.loop.create_server(self.app.make_handler(), host, port)

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
            response = await self.query_user(user_id)
        except Exception:
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
            response = await self.query_alias(alias)
        except Exception:
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

    async def update_state_store(self, event):
        event_type = event["type"]
        if event_type == "m.room.power_levels":
            self.state_store.set_power_levels(event["room_id"], event["content"])
        elif event_type == "m.room.member":
            self.state_store.set_membership(event["room_id"], event["state_key"],
                                            event["content"]["membership"])

    def handle_matrix_event(self, event):
        async def try_handle(handler):
            try:
                await handler(event)
            except Exception:
                self.log.exception("Exception in Matrix event handler")

        for handler in self.event_handlers:
            asyncio.ensure_future(try_handle(handler), loop=self.loop)

    def matrix_event_handler(self, func):
        self.event_handlers.append(func)
        return func
