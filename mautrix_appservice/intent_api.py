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
from matrix_client.api import MatrixHttpApi
from matrix_client.errors import MatrixRequestError


class HTTPAPI(MatrixHttpApi):
    def __init__(self, base_url, bot_mxid=None, token=None, identity=None, log=None):
        self.base_url = base_url
        self.token = token
        self.identity = identity
        self.txn_id = 0
        self.bot_mxid = bot_mxid
        self.log = log
        self.validate_cert = True
        self.children = {}

    def user(self, user):
        try:
            return self.children[user]
        except KeyError:
            child = ChildHTTPAPI(user, self)
            self.children[user] = child
            return child

    def bot_intent(self):
        return IntentAPI(self.bot_mxid, self, log=self.log)

    def intent(self, user):
        return IntentAPI(user, self.user(user), self, log=self.log)

    def _send(self, method, path, content=None, query_params={}, headers={},
              api_path="/_matrix/client/r0"):
        if not query_params:
            query_params = {}
        query_params["user_id"] = self.identity
        log_content = content if not isinstance(content, bytes) else f"<{len(content)} bytes>"
        self.log.debug("%s %s %s", method, path, log_content)
        return super()._send(method, path, content, query_params, headers, api_path=api_path)

    def create_room(self, alias=None, is_public=False, name=None, topic=None, is_direct=False,
                    invitees=()):
        """Perform /createRoom.
        Args:
            alias (str): Optional. The room alias name to set for this room.
            is_public (bool): Optional. The public/private visibility.
            name (str): Optional. The name for the room.
            topic (str): Optional. The topic for the room.
            invitees (list<str>): Optional. The list of user IDs to invite.
        """
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
        self.identity = user
        self.token = parent.token
        self.base_url = parent.base_url
        self.validate_cert = parent.validate_cert
        self.log = parent.log
        self.parent = parent

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
    except:
        return err.content


class IntentAPI:
    mxid_regex = re.compile("@(.+):(.+)")

    def __init__(self, mxid, client, bot=None, log=None):
        self.client = client
        self.bot = bot
        self.mxid = mxid
        self.log = log

        results = self.mxid_regex.search(mxid)
        if not results:
            raise ValueError("invalid MXID")
        self.localpart = results.group(1)

        self.memberships = {}
        self.power_levels = {}
        self.registered = False

    def user(self, user):
        if not self.bot:
            return self.client.intent(user)
        else:
            raise ValueError("IntentAPI#user() is only available for base intent objects.")

    # region User actions

    def set_display_name(self, name):
        self._ensure_registered()
        return self.client.set_display_name(self.mxid, name)

    def set_presence(self, status="online"):
        self._ensure_registered()
        return self.client.set_presence(status)

    def set_avatar(self, url):
        self._ensure_registered()
        return self.client.set_avatar_url(self.mxid, url)

    def media_upload(self, photo_data, mime_type=None):
        self._ensure_registered()
        mime_type = mime_type or magic.from_buffer(photo_data, mime=True)
        return self.client.media_upload(photo_data, mime_type)

    # endregion
    # region Room actions

    def create_room(self, alias=None, is_public=False, name=None, topic=None, is_direct=False,
                    invitees=()):
        self._ensure_registered()
        return self.client.create_room(alias, is_public, name, topic, is_direct, invitees)

    def invite(self, room_id, user_id):
        self._ensure_joined(room_id)
        try:
            return self.client.invite_user(room_id, user_id)
        except MatrixRequestError as e:
            if matrix_error_code(e) != "M_FORBIDDEN":
                raise IntentError(f"Failed to invite {user_id} to {room_id}", e)


    def set_room_avatar(self, room_id, avatar_url, info=None):
        content = {
            "url": avatar_url,
        }
        if info:
            content["info"] = info
        return self.send_state_event(room_id, "m.room.avatar", content)

    def set_room_name(self, room_id, name):
        self._ensure_joined(room_id)
        self._ensure_has_power_level_for(room_id, "m.room.name")
        return self.client.set_room_name(room_id, name)

    def set_typing(self, room_id, is_typing=True, timeout=5000):
        self._ensure_joined(room_id)
        return self.client.set_typing(room_id, is_typing, timeout)

    def send_notice(self, room_id, text, html=None):
        self.send_text(room_id, text, html, "m.notice")

    def send_emote(self, room_id, text, html=None):
        self.send_text(room_id, text, html, "m.emote")

    def send_text(self, room_id, text, html=None, type="m.text"):
        if html:
            return self.send_message(room_id, {
                "body": text,
                "msgtype": type,
                "format": "org.matrix.custom.html",
                "formatted_body": html or text,
            })
        else:
            return self.send_message(room_id, {
                "body": text,
                "msgtype": type,
            })

    def send_message(self, room_id, body):
        return self.send_event(room_id, "m.room.message", body)

    def send_event(self, room_id, type, body, txn_id=None, timestamp=None):
        self._ensure_joined(room_id)
        self._ensure_has_power_level_for(room_id, type)
        return self.client.send_message_event(room_id, type, body, txn_id, timestamp)

    def send_state_event(self, room_id, type, body, state_key="", timestamp=None):
        self._ensure_joined(room_id)
        self._ensure_has_power_level_for(room_id, type)
        return self.client.send_state_event(room_id, type, body, state_key, timestamp)

    def join_room(self, room_id):
        return self._ensure_joined(room_id, ignore_cache=True)

    def leave_room(self, room_id):
        self.memberships[room_id] = "left"
        return self.client.leave_room(room_id)

    def get_room_members(self, room_id):
        return self.client.get_room_members(room_id)

    def get_joined_users(self, room_id):
        memberships = self.get_room_members(room_id)
        return [membership["state_key"] for membership in memberships["chunk"] if
                membership["content"]["membership"] == "join"]

    # endregion
    # region Ensure functions

    def _ensure_joined(self, room_id, ignore_cache=False):
        if not ignore_cache and self.memberships.get(room_id, "") == "join":
            return
        self._ensure_registered()
        try:
            self.client.join_room(room_id)
            self.memberships[room_id] = "join"
        except MatrixRequestError as e:
            if matrix_error_code(e) != "M_FORBIDDEN" and not self.bot:
                raise IntentError(f"Failed to join room {room_id} as {self.mxid}", e)
            try:
                self.bot.invite_user(room_id, self.mxid)
                self.client.join_room(room_id)
                self.memberships[room_id] = "join"
            except MatrixRequestError as e2:
                raise IntentError(f"Failed to join room {room_id} as {self.mxid}", e2)

    def _ensure_registered(self):
        if self.registered:
            return
        try:
            self.client.register({"username": self.localpart})
        except MatrixRequestError as e:
            if matrix_error_code(e) != "M_USER_IN_USE":
                raise IntentError(f"Failed to register {self.mxid}", e)
        self.registered = True

    def _ensure_has_power_level_for(self, room_id, event_type):
        # TODO implement
        pass

    # endregion
