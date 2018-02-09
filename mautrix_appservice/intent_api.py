# -*- coding: future_fstrings -*-
# mautrix-telegram - A Matrix-Telegram puppeting bridge
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
import re
import json
import magic
import urllib.request

from matrix_client.errors import MatrixRequestError

from .temp_async_api import AsyncHTTPAPI


class HTTPAPI(AsyncHTTPAPI):
    def __init__(self, base_url, domain=None, bot_mxid=None, token=None, identity=None, log=None,
                 state_store=None, client_session=None):
        super().__init__(base_url, client_session, token, identity)
        self.domain = domain
        self.bot_mxid = bot_mxid
        self.intent_log = log.getChild("intent")
        self.log = log.getChild("api")
        self.validate_cert = True
        self.state_store = state_store
        self.children = {}

    def user(self, user):
        try:
            return self.children[user]
        except KeyError:
            child = ChildHTTPAPI(user, self)
            self.children[user] = child
            return child

    def bot_intent(self):
        return IntentAPI(self.bot_mxid, self, state_store=self.state_store, log=self.intent_log)

    def intent(self, user):
        return IntentAPI(user, self.user(user), self, self.state_store, self.intent_log)

    def _send(self, method, path, content=None, query_params=None, headers=None,
              api_path="/_matrix/client/r0"):
        if not query_params:
            query_params = {}
        if self.identity:
            query_params["user_id"] = self.identity
        log_content = content if not isinstance(content, bytes) else f"<{len(content)} bytes>"
        self.log.debug("%s %s %s", method, path, log_content)
        return super()._send(method, path, content, query_params, headers or {}, api_path=api_path)

    def create_room(self, alias=None, is_public=False, name=None, topic=None, is_direct=False,
                    invitees=(), initial_state=None):
        content = {
            "visibility": "public" if is_public else "private"
        }
        if alias:
            content["room_alias_name"] = alias
        if invitees:
            content["invite"] = invitees
        if name:
            content["name"] = name
        if topic:
            content["topic"] = topic
        if initial_state:
            content["initial_state"] = initial_state
        content["is_direct"] = is_direct

        return self._send("POST", "/createRoom", content)

    def set_presence(self, status="online", user=None):
        content = {
            "presence": status
        }
        user = user or self.identity
        return self._send("PUT", f"/presence/{user}/status", content)

    def set_typing(self, room_id, is_typing=True, timeout=5000, user=None):
        content = {
            "typing": is_typing
        }
        if is_typing:
            content["timeout"] = timeout
        user = user or self.identity
        return self._send("PUT", f"/rooms/{room_id}/typing/{user}", content)


class ChildHTTPAPI(HTTPAPI):
    def __init__(self, user, parent):
        self.base_url = parent.base_url
        self.token = parent.token
        self.identity = user
        self.validate_cert = True
        self.validate_cert = parent.validate_cert
        self.log = parent.log
        self.domain = parent.domain
        self.parent = parent
        self.client_session = parent.client_session

    @property
    def txn_id(self):
        return self.parent.txn_id

    @txn_id.setter
    def txn_id(self, value):
        self.parent.txn_id = value


class IntentError(Exception):
    def __init__(self, message, source):
        super().__init__(message)
        self.source = source


def matrix_error_code(err):
    try:
        data = json.loads(err.content)
        return data["errcode"]
    except Exception:
        return err.content


def matrix_error_data(err):
    try:
        data = json.loads(err.content)
        return data["errcode"], data["error"]
    except Exception:
        return err.content


class IntentAPI:
    mxid_regex = re.compile("@(.+):(.+)")

    def __init__(self, mxid, client, bot=None, state_store=None, log=None):
        self.client = client
        self.bot = bot
        self.mxid = mxid
        self.log = log

        results = self.mxid_regex.search(mxid)
        if not results:
            raise ValueError("invalid MXID")
        self.localpart = results.group(1)

        self.state_store = state_store

    def user(self, user):
        if not self.bot:
            return self.client.intent(user)
        else:
            self.log.warning("Called IntentAPI#user() of child intent object.")
            return self.bot.intent(user)

    # region User actions

    async def get_joined_rooms(self):
        await self.ensure_registered()
        response = await self.client._send("GET", "/joined_rooms")
        return response["joined_rooms"]

    async def set_display_name(self, name):
        await self.ensure_registered()
        return await self.client.set_display_name(self.mxid, name)

    async def set_presence(self, status="online"):
        await self.ensure_registered()
        return await self.client.set_presence(status)

    async def set_avatar(self, url):
        await self.ensure_registered()
        return await self.client.set_avatar_url(self.mxid, url)

    async def upload_file(self, data, mime_type=None):
        await self.ensure_registered()
        mime_type = mime_type or magic.from_buffer(data, mime=True)
        return await self.client.media_upload(data, mime_type)

    async def download_file(self, url):
        await self.ensure_registered()
        url = self.client.get_download_url(url)
        async with self.client.client_session.get(url) as response:
            return await response.read()

    # endregion
    # region Room actions

    async def create_room(self, alias=None, is_public=False, name=None, topic=None, is_direct=False,
                    invitees=(), initial_state=None):
        await self.ensure_registered()
        return await self.client.create_room(alias, is_public, name, topic, is_direct, invitees,
                                       initial_state or {})

    async def invite(self, room_id, user_id, check_cache=False):
        await self.ensure_joined(room_id)
        try:
            ok_states = {"invite", "join"}
            do_invite = (not check_cache
                         or self.state_store.get_membership(room_id, user_id) not in ok_states)
            if do_invite:
                response = await self.client.invite_user(room_id, user_id)
                self.state_store.invited(room_id, user_id)
                return response
        except MatrixRequestError as e:
            code, message = matrix_error_data(e)
            if code != "M_FORBIDDEN":
                raise IntentError(f"Failed to invite {user_id} to {room_id}", e)
            if "is already in the room" in message:
                self.state_store.joined(room_id, user_id)

    def set_room_avatar(self, room_id, avatar_url, info=None):
        content = {
            "url": avatar_url,
        }
        if info:
            content["info"] = info
        return self.send_state_event(room_id, "m.room.avatar", content)

    async def add_room_alias(self, room_id, alias):
        await self.ensure_registered()
        return await self.client.set_room_alias(room_id, f"#{alias}:{self.client.domain}")

    async def remove_room_alias(self, alias):
        await self.ensure_registered()
        return await self.client.remove_room_alias(f"#{alias}:{self.client.domain}")

    async def set_room_name(self, room_id, name):
        await self.ensure_joined(room_id)
        self._ensure_has_power_level_for(room_id, "m.room.name")
        return await self.client.set_room_name(room_id, name)

    async def get_power_levels(self, room_id, ignore_cache=False):
        await self.ensure_joined(room_id)
        if not ignore_cache:
            try:
                return self.state_store.get_power_levels(room_id)
            except KeyError:
                pass
        levels = await self.client.get_power_levels(room_id)
        self.state_store.set_power_levels(room_id, levels)
        return levels

    async def set_power_levels(self, room_id, content):
        response = await self.send_state_event(room_id, "m.room.power_levels", content)
        self.state_store.set_power_levels(room_id, content)
        return response

    async def get_pinned_messages(self, room_id):
        await self.ensure_joined(room_id)
        response = await self.client._send("GET", f"/rooms/{room_id}/state/m.room.pinned_events")
        return response["content"]["pinned"]

    def set_pinned_messages(self, room_id, events):
        return self.send_state_event(room_id, "m.room.pinned_events", {
            "pinned": events
        })

    async def pin_message(self, room_id, event_id):
        events = await self.get_pinned_messages(room_id)
        if event_id not in events:
            events.append(event_id)
            await self.set_pinned_messages(room_id, events)

    async def unpin_message(self, room_id, event_id):
        events = await self.get_pinned_messages(room_id)
        if event_id in events:
            events.remove(event_id)
            await self.set_pinned_messages(room_id, events)

    async def get_event(self, room_id, event_id):
        await self.ensure_joined(room_id)
        return await self.client._send("GET", f"/rooms/{room_id}/event/{event_id}")

    async def set_typing(self, room_id, is_typing=True, timeout=5000):
        await self.ensure_joined(room_id)
        return await self.client.set_typing(room_id, is_typing, timeout)

    async def mark_read(self, room_id, event_id):
        await self.ensure_joined(room_id)
        return await self.client._send("POST", f"/rooms/{room_id}/receipt/m.read/{event_id}", content={})

    def send_notice(self, room_id, text, html=None):
        return self.send_text(room_id, text, html, "m.notice")

    def send_emote(self, room_id, text, html=None):
        return self.send_text(room_id, text, html, "m.emote")

    def send_image(self, room_id, url, info=None, text=None):
        return self.send_file(room_id, url, info or {}, text, "m.image")

    def send_file(self, room_id, url, info=None, text=None, file_type="m.file"):
        return self.send_message(room_id, {
            "msgtype": file_type,
            "url": url,
            "body": text or "Uploaded file",
            "info": info or {},
        })

    def send_text(self, room_id, text, html=None, msgtype="m.text"):
        if html:
            if not text:
                text = html
            return self.send_message(room_id, {
                "body": text,
                "msgtype": msgtype,
                "format": "org.matrix.custom.html",
                "formatted_body": html or text,
            })
        else:
            return self.send_message(room_id, {
                "body": text,
                "msgtype": msgtype,
            })

    def send_message(self, room_id, body):
        return self.send_event(room_id, "m.room.message", body)

    async def error_and_leave(self, room_id, text, html=None):
        await self.ensure_joined(room_id)
        await self.send_notice(room_id, text, html=html)
        await self.leave_room(room_id)

    async def kick(self, room_id, user_id, message):
        await self.ensure_joined(room_id)
        return await self.client.kick_user(room_id, user_id, message)

    async def send_event(self, room_id, event_type, body, txn_id=None):
        await self.ensure_joined(room_id)
        self._ensure_has_power_level_for(room_id, event_type)
        return await self.client.send_message_event(room_id, event_type, body, txn_id)

    async def send_state_event(self, room_id, event_type, body, state_key=""):
        await self.ensure_joined(room_id)
        self._ensure_has_power_level_for(room_id, event_type)
        return await self.client.send_state_event(room_id, event_type, body, state_key)

    def join_room(self, room_id):
        return self.ensure_joined(room_id, ignore_cache=True)

    def leave_room(self, room_id):
        self.state_store.left(room_id, self.mxid)
        return self.client.leave_room(room_id)

    def get_room_memberships(self, room_id):
        return self.client.get_room_members(room_id)

    async def get_room_members(self, room_id, allowed_memberships=("join",)):
        memberships = await self.get_room_memberships(room_id)
        return [membership["state_key"] for membership in memberships["chunk"] if
                membership["content"]["membership"] in allowed_memberships]

    async def get_room_state(self, room_id):
        await self.ensure_joined(room_id)
        state = await self.client.get_room_state(room_id)
        return state

    # endregion
    # region Ensure functions

    async def ensure_joined(self, room_id, ignore_cache=False):
        if not ignore_cache and self.state_store.is_joined(room_id, self.mxid):
            return
        await self.ensure_registered()
        try:
            await self.client.join_room(room_id)
            self.state_store.joined(room_id, self.mxid)
        except MatrixRequestError as e:
            if matrix_error_code(e) != "M_FORBIDDEN" or not self.bot:
                raise IntentError(f"Failed to join room {room_id} as {self.mxid}", e)
            try:
                self.bot.invite_user(room_id, self.mxid)
                self.client.join_room(room_id)
                self.state_store.joined(room_id, self.mxid)
            except MatrixRequestError as e2:
                raise IntentError(f"Failed to join room {room_id} as {self.mxid}", e2)

    async def ensure_registered(self):
        if self.state_store.is_registered(self.mxid):
            return
        try:
            await self.client.register({"username": self.localpart})
        except MatrixRequestError as e:
            if matrix_error_code(e) != "M_USER_IN_USE":
                self.log.exception(f"Failed to register {self.mxid}!")
                # raise IntentError(f"Failed to register {self.mxid}", e)
                return
        self.state_store.registered(self.mxid)

    def _ensure_has_power_level_for(self, room_id, event_type):
        if not self.state_store.has_power_levels(room_id):
            self.get_power_levels(room_id)
        if self.state_store.has_power_level(room_id, self.mxid, event_type):
            return
        elif not self.bot:
            self.log.warning(
                f"Power level of {self.mxid} is not enough for {event_type} in {room_id}")
            # raise IntentError(f"Power level of {self.mxid} is not enough"
            #                  + f"for {event_type} in {room_id}")
            return
        # TODO implement

    # endregion
