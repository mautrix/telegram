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
from urllib.parse import quote
from time import time
from json.decoder import JSONDecodeError
from aiohttp.client_exceptions import ContentTypeError
import re
import json
import magic
import asyncio

from .errors import MatrixError, MatrixRequestError, IntentError


class HTTPAPI:
    def __init__(self, base_url, domain=None, bot_mxid=None, token=None, identity=None, log=None,
                 state_store=None, client_session=None, child=False):
        self.base_url = base_url
        self.token = token
        self.identity = identity
        self.validate_cert = True
        self.session = client_session

        self.domain = domain
        self.bot_mxid = bot_mxid
        self._bot_intent = None
        self.state_store = state_store

        if child:
            self.log = log
        else:
            self.intent_log = log.getChild("intent")
            self.log = log.getChild("api")
            self.txn_id = 0
            self.children = {}

    def user(self, user):
        try:
            return self.children[user]
        except KeyError:
            child = ChildHTTPAPI(user, self)
            self.children[user] = child
            return child

    def bot_intent(self):
        if self._bot_intent:
            return self._bot_intent
        return IntentAPI(self.bot_mxid, self, state_store=self.state_store, log=self.intent_log)

    def intent(self, user):
        return IntentAPI(user, self.user(user), self.bot_intent(), self.state_store,
                         self.intent_log)

    async def _send(self, method, endpoint, content, query_params, headers):
        while True:
            query_params["access_token"] = self.token
            request = self.session.request(method, endpoint, params=query_params,
                                           data=content, headers=headers)
            async with request as response:
                if response.status < 200 or response.status >= 300:
                    errcode = message = None
                    try:
                        response_data = await response.json()
                        errcode = response_data["errcode"]
                        message = response_data["error"]
                    except (JSONDecodeError, ContentTypeError, KeyError):
                        pass
                    raise MatrixRequestError(code=response.status, text=await response.text(),
                                             errcode=errcode, message=message)

                if response.status == 429:
                    await asyncio.sleep(response.json()["retry_after_ms"] / 1000)
                else:
                    return await response.json()

    def _log_request(self, method, path, content, query_params):
        log_content = content if not isinstance(content, bytes) else f"<{len(content)} bytes>"
        log_content = log_content or "(No content)"
        query_identity = query_params["user_id"] if "user_id" in query_params else "No identity"
        self.log.debug("%s %s %s as user %s", method, path, log_content, query_identity)

    def request(self, method, path, content=None, query_params=None, headers=None,
                api_path="/_matrix/client/r0"):
        content = content or {}
        query_params = query_params or {}
        headers = headers or {}

        method = method.upper()
        if method not in ["GET", "PUT", "DELETE", "POST"]:
            raise MatrixError("Unsupported HTTP method: %s" % method)

        if "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"
        if headers["Content-Type"] == "application/json":
            content = json.dumps(content)

        if self.identity:
            query_params["user_id"] = self.identity

        self._log_request(method, path, content, query_params)

        endpoint = self.base_url + api_path + path
        return self._send(method, endpoint, content, query_params, headers or {})

    def get_download_url(self, mxcurl):
        if mxcurl.startswith('mxc://'):
            return f"{self.base_url}/_matrix/media/r0/download/{mxcurl[6:]}"
        else:
            raise ValueError("MXC URL did not begin with 'mxc://'")

    async def get_display_name(self, user_id):
        content = await self.request("GET", f"/profile/{user_id}/displayname")
        return content.get('displayname', None)

    async def get_avatar_url(self, user_id):
        content = await self.request("GET", f"/profile/{user_id}/avatar_url")
        return content.get('avatar_url', None)

    async def get_room_id(self, room_alias):
        content = await self.request("GET", f"/directory/room/{quote(room_alias)}")
        return content.get("room_id", None)

    def set_typing(self, room_id, is_typing=True, timeout=5000, user=None):
        content = {
            "typing": is_typing
        }
        if is_typing:
            content["timeout"] = timeout
        user = user or self.identity
        return self.request("PUT", f"/rooms/{room_id}/typing/{user}", content)


class ChildHTTPAPI(HTTPAPI):
    def __init__(self, user, parent):
        super().__init__(parent.base_url, parent.domain, parent.bot_mxid, parent.token, user,
                         parent.log, parent.state_store, parent.session, child=True)
        self.parent = parent

    @property
    def txn_id(self):
        return self.parent.txn_id

    @txn_id.setter
    def txn_id(self, value):
        self.parent.txn_id = value


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
            return self.bot.client.intent(user)

    # region User actions

    async def get_joined_rooms(self):
        await self.ensure_registered()
        response = await self.client.request("GET", "/joined_rooms")
        return response["joined_rooms"]

    async def set_display_name(self, name):
        await self.ensure_registered()
        content = {"displayname": name}
        return await self.client.request("PUT", f"/profile/{self.mxid}/displayname", content)

    async def set_presence(self, status="online"):
        await self.ensure_registered()
        content = {
            "presence": status
        }
        return await self.client.request("PUT", f"/presence/{self.mxid}/status", content)

    async def set_avatar(self, url):
        await self.ensure_registered()
        content = {"avatar_url": url}
        return await self.client.request("PUT", f"/profile/{self.mxid}/avatar_url", content)

    async def upload_file(self, data, mime_type=None):
        await self.ensure_registered()
        mime_type = mime_type or magic.from_buffer(data, mime=True)
        return await self.client.request("POST", "", content=data,
                                         headers={"Content-Type": mime_type},
                                         api_path="/_matrix/media/r0/upload")

    async def download_file(self, url):
        await self.ensure_registered()
        url = self.client.get_download_url(url)
        async with self.client.session.get(url) as response:
            return await response.read()

    # endregion
    # region Room actions

    async def create_room(self, alias=None, is_public=False, name=None, topic=None,
                          is_direct=False, invitees=None, initial_state=None):
        await self.ensure_registered()
        content = {
            "visibility": "public" if is_public else "private",
            "is_direct": is_direct,
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

        return await self.client.request("POST", "/createRoom", content)

    def _invite_direct(self, room_id, user_id):
        content = {"user_id": user_id}
        return self.client.request("POST", "/rooms/" + room_id + "/invite", content)

    async def invite(self, room_id, user_id, check_cache=False):
        await self.ensure_joined(room_id)
        try:
            ok_states = {"invite", "join"}
            do_invite = (not check_cache
                         or self.state_store.get_membership(room_id, user_id) not in ok_states)
            if do_invite:
                response = await self._invite_direct(room_id, user_id)
                self.state_store.invited(room_id, user_id)
                return response
        except MatrixRequestError as e:
            if e.errcode != "M_FORBIDDEN":
                raise IntentError(f"Failed to invite {user_id} to {room_id}", e)
            if "is already in the room" in e.message:
                self.state_store.joined(room_id, user_id)

    def set_room_avatar(self, room_id, avatar_url, info=None):
        content = {
            "url": avatar_url,
        }
        if info:
            content["info"] = info
        return self.send_state_event(room_id, "m.room.avatar", content)

    async def add_room_alias(self, room_id, localpart):
        await self.ensure_registered()
        content = {"room_id": room_id}
        alias = f"#{localpart}:{self.client.domain}"
        return await self.client.request("PUT", f"/directory/room/{quote(alias)}", content)

    async def remove_room_alias(self, localpart):
        await self.ensure_registered()
        alias = f"#{localpart}:{self.client.domain}"
        return await self.client.request("DELETE", f"/directory/room/{quote(alias)}")

    def set_room_name(self, room_id, name):
        body = {"name": name}
        return self.send_state_event(room_id, "m.room.name", body)

    async def get_power_levels(self, room_id, ignore_cache=False):
        await self.ensure_joined(room_id)
        if not ignore_cache:
            try:
                return self.state_store.get_power_levels(room_id)
            except KeyError:
                pass
        levels = await self.client.request("GET",
                                           f"/rooms/{quote(room_id)}/state/m.room.power_levels")
        self.state_store.set_power_levels(room_id, levels)
        return levels

    async def set_power_levels(self, room_id, content):
        if "events" not in content:
            content["events"] = {}
        response = await self.send_state_event(room_id, "m.room.power_levels", content)
        self.state_store.set_power_levels(room_id, content)
        return response

    async def get_pinned_messages(self, room_id):
        await self.ensure_joined(room_id)
        response = await self.client.request("GET", f"/rooms/{room_id}/state/m.room.pinned_events")
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
        return await self.client.request("GET", f"/rooms/{room_id}/event/{event_id}")

    async def set_typing(self, room_id, is_typing=True, timeout=5000):
        await self.ensure_joined(room_id)
        content = {
            "typing": is_typing
        }
        if is_typing:
            content["timeout"] = timeout
        return await self.client.request("PUT", f"/rooms/{room_id}/typing/{self.mxid}", content)

    async def mark_read(self, room_id, event_id):
        await self.ensure_joined(room_id)
        return await self.client.request("POST", f"/rooms/{room_id}/receipt/m.read/{event_id}",
                                         content={})

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

    def kick(self, room_id, user_id, message):
        return self.set_membership(room_id, user_id, "leave", message)

    def get_membership(self, room_id, user_id):
        return self.get_state_event(room_id, "m.room.member", state_key=user_id)

    def set_membership(self, room_id, user_id, membership, reason="", profile=None):
        body = {
            "membership": membership,
            "reason": reason
        }
        profile = profile or {}
        if "displayname" in profile:
            body["displayname"] = profile["displayname"]
        if "avatar_url" in profile:
            body["avatar_url"] = profile["avatar_url"]

        return self.send_state_event(room_id, "m.room.member", body, state_key=user_id)

    @staticmethod
    def _get_event_url(room_id, event_type, txn_id):
        return f"/rooms/{quote(room_id)}/send/{quote(event_type)}/{quote(txn_id)}"

    async def send_event(self, room_id, event_type, content, txn_id=None):
        await self.ensure_joined(room_id)
        await self._ensure_has_power_level_for(room_id, event_type)

        txn_id = txn_id or str(self.client.txn_id) + str(int(time() * 1000))
        self.client.txn_id += 1

        url = self._get_event_url(room_id, event_type, txn_id)

        return await self.client.request("PUT", url, content)

    @staticmethod
    def _get_state_url(room_id, event_type, state_key=""):
        url = f"/rooms/{quote(room_id)}/state/{quote(event_type)}"
        if state_key:
            url += f"/{quote(state_key)}"
        return url

    async def send_state_event(self, room_id, event_type, content, state_key=""):
        await self.ensure_joined(room_id)
        await self._ensure_has_power_level_for(room_id, event_type)
        url = self._get_state_url(room_id, event_type, state_key)
        return await self.client.request("PUT", url, content)

    async def get_state_event(self, room_id, event_type, state_key=""):
        await self.ensure_joined(room_id)
        url = self._get_state_url(room_id, event_type, state_key)
        return await self.client.request("GET", url)

    def join_room(self, room_id):
        return self.ensure_joined(room_id, ignore_cache=True)

    def _join_room_direct(self, room):
        return self.client.request("POST", f"/join/{quote(room)}")

    def leave_room(self, room_id):
        try:
            self.state_store.left(room_id, self.mxid)
            return self.client.request("POST", f"/rooms/{quote(room_id)}/leave")
        except MatrixRequestError as e:
            if "not in room" not in e.message:
                raise

    def get_room_memberships(self, room_id):
        return self.client.request("GET", f"/rooms/{quote(room_id)}/members")

    async def get_room_members(self, room_id, allowed_memberships=("join",)):
        memberships = await self.get_room_memberships(room_id)
        return [membership["state_key"] for membership in memberships["chunk"] if
                membership["content"]["membership"] in allowed_memberships]

    async def get_room_state(self, room_id):
        await self.ensure_joined(room_id)
        state = await self.client.request("GET", f"/rooms/{quote(room_id)}/state")
        # TODO update values based on state?
        return state

    # endregion
    # region Ensure functions

    async def ensure_joined(self, room_id, ignore_cache=False):
        if not ignore_cache and self.state_store.is_joined(room_id, self.mxid):
            return
        await self.ensure_registered()
        try:
            await self._join_room_direct(room_id)
            self.state_store.joined(room_id, self.mxid)
        except MatrixRequestError as e:
            if e.errcode != "M_FORBIDDEN" or not self.bot:
                raise IntentError(f"Failed to join room {room_id} as {self.mxid}", e)
            try:
                await self.bot.invite(room_id, self.mxid)
                await self._join_room_direct(room_id)
                self.state_store.joined(room_id, self.mxid)
            except MatrixRequestError as e2:
                raise IntentError(f"Failed to join room {room_id} as {self.mxid}", e2)

    def _register(self):
        content = {"username": self.localpart}
        query_params = {"kind": "user"}
        return self.client.request("POST", "/register", content, query_params)

    async def ensure_registered(self):
        if self.state_store.is_registered(self.mxid):
            return
        try:
            await self._register()
        except MatrixRequestError as e:
            if e.errcode != "M_USER_IN_USE":
                self.log.exception(f"Failed to register {self.mxid}!")
                # raise IntentError(f"Failed to register {self.mxid}", e)
                return
        self.state_store.registered(self.mxid)

    async def _ensure_has_power_level_for(self, room_id, event_type):
        if not self.state_store.has_power_levels(room_id):
            await self.get_power_levels(room_id)
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
