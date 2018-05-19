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
import platform
import os

from telethon.tl.types import *
from mautrix_appservice import MatrixRequestError

from .tgclient import MautrixTelegramClient
from .db import Message as DBMessage
from . import portal as po, puppet as pu, __version__

config = None
# Value updated from config in init()
MAX_DELETIONS = 10


class AbstractUser:
    session_container = None
    loop = None
    log = None
    db = None
    az = None

    def __init__(self):
        self.connected = False
        self.whitelisted = False
        self.client = None
        self.tgid = None
        self.mxid = None
        self.is_relaybot = False

    async def _init_client(self):
        self.log.debug(f"Initializing client for {self.name}")
        device = f"{platform.system()} {platform.release()}"
        sysversion = MautrixTelegramClient.__version__
        self.session = self.session_container.new_session(self.name)
        self.client = MautrixTelegramClient(session=self.session,
                                            api_id=config["telegram.api_id"],
                                            api_hash=config["telegram.api_hash"],
                                            loop=self.loop,
                                            app_version=__version__,
                                            system_version=sysversion,
                                            device_model=device,
                                            report_errors=False)
        await self.client.add_event_handler(self._update_catch)

    async def update(self, update):
        return False

    async def post_login(self):
        raise NotImplementedError()

    async def _update_catch(self, update):
        try:
            if not await self.update(update):
                await self._update(update)
        except Exception:
            self.log.exception("Failed to handle Telegram update")

    async def _get_dialogs(self, limit=None):
        dialogs = await self.client.get_dialogs(limit=limit)
        return [dialog.entity for dialog in dialogs if (
            not isinstance(dialog.entity, (User, ChatForbidden, ChannelForbidden))
            and not (isinstance(dialog.entity, Chat)
                     and (dialog.entity.deactivated or dialog.entity.left)))]

    @property
    def name(self):
        raise NotImplementedError()

    @property
    def logged_in(self):
        return self.client and self.client.is_user_authorized()

    @property
    def has_full_access(self):
        return self.logged_in and self.whitelisted

    async def start(self):
        if not self.client:
            await self._init_client()
        self.connected = await self.client.connect()

    async def ensure_started(self, even_if_no_session=False):
        if not self.whitelisted:
            return self
        elif not self.connected and (even_if_no_session or os.path.exists(f"{self.name}.session")):
            return await self.start()
        return self

    def stop(self):
        self.client.disconnect()
        self.client = None
        self.connected = False

    # region Telegram update handling

    async def _update(self, update):
        if isinstance(update, (UpdateShortChatMessage, UpdateShortMessage, UpdateNewChannelMessage,
                               UpdateNewMessage, UpdateEditMessage, UpdateEditChannelMessage)):
            await self.update_message(update)
        elif isinstance(update, UpdateDeleteMessages):
            await self.delete_message(update)
        elif isinstance(update, UpdateDeleteChannelMessages):
            await self.delete_channel_message(update)
        elif isinstance(update, (UpdateChatUserTyping, UpdateUserTyping)):
            await self.update_typing(update)
        elif isinstance(update, UpdateUserStatus):
            await self.update_status(update)
        elif isinstance(update, (UpdateChatAdmins, UpdateChatParticipantAdmin)):
            await self.update_admin(update)
        elif isinstance(update, UpdateChatParticipants):
            await self.update_participants(update)
        elif isinstance(update, UpdateChannelPinnedMessage):
            await self.update_pinned_messages(update)
        elif isinstance(update, (UpdateUserName, UpdateUserPhoto)):
            await self.update_others_info(update)
        elif isinstance(update, UpdateReadHistoryOutbox):
            await self.update_read_receipt(update)
        else:
            self.log.debug("Unhandled update: %s", update)

    async def update_pinned_messages(self, update):
        portal = po.Portal.get_by_tgid(update.channel_id)
        if portal and portal.mxid:
            await portal.receive_telegram_pin_id(update.id)

    async def update_participants(self, update):
        portal = po.Portal.get_by_tgid(update.participants.chat_id)
        if portal and portal.mxid:
            await portal.update_telegram_participants(update.participants.participants)

    async def update_read_receipt(self, update):
        if not isinstance(update.peer, PeerUser):
            self.log.debug("Unexpected read receipt peer: %s", update.peer)
            return

        portal = po.Portal.get_by_tgid(update.peer.user_id, self.tgid)
        if not portal or not portal.mxid:
            return

        # We check that these are user read receipts, so tg_space is always the user ID.
        message = DBMessage.query.get((update.max_id, self.tgid))
        if not message:
            return

        puppet = pu.Puppet.get(update.peer.user_id)
        await puppet.intent.mark_read(portal.mxid, message.mxid)

    async def update_admin(self, update):
        # TODO duplication not checked
        portal = po.Portal.get_by_tgid(update.chat_id, peer_type="chat")
        if isinstance(update, UpdateChatAdmins):
            await portal.set_telegram_admins_enabled(update.enabled)
        elif isinstance(update, UpdateChatParticipantAdmin):
            await portal.set_telegram_admin(update.user_id)
        else:
            self.log.warning("Unexpected admin status update: %s", update)

    async def update_typing(self, update):
        if isinstance(update, UpdateUserTyping):
            portal = po.Portal.get_by_tgid(update.user_id, self.tgid, "user")
        else:
            portal = po.Portal.get_by_tgid(update.chat_id, peer_type="chat")
        sender = pu.Puppet.get(update.user_id)
        await portal.handle_telegram_typing(sender, update)

    async def update_others_info(self, update):
        # TODO duplication not checked
        puppet = pu.Puppet.get(update.user_id)
        if isinstance(update, UpdateUserName):
            if await puppet.update_displayname(self, update):
                puppet.save()
        elif isinstance(update, UpdateUserPhoto):
            if await puppet.update_avatar(self, update.photo.photo_big):
                puppet.save()
        else:
            self.log.warning("Unexpected other user info update: %s", update)

    async def update_status(self, update):
        puppet = pu.Puppet.get(update.user_id)
        if isinstance(update.status, UserStatusOnline):
            await puppet.intent.set_presence("online")
        elif isinstance(update.status, UserStatusOffline):
            await puppet.intent.set_presence("offline")
        else:
            self.log.warning("Unexpected user status update: %s", update)
        return

    def get_message_details(self, update):
        if isinstance(update, UpdateShortChatMessage):
            portal = po.Portal.get_by_tgid(update.chat_id, peer_type="chat")
            sender = pu.Puppet.get(update.from_id)
        elif isinstance(update, UpdateShortMessage):
            portal = po.Portal.get_by_tgid(update.user_id, self.tgid, "user")
            sender = pu.Puppet.get(self.tgid if update.out else update.user_id)
        elif isinstance(update, (UpdateNewMessage, UpdateNewChannelMessage,
                                 UpdateEditMessage, UpdateEditChannelMessage)):
            update = update.message
            if isinstance(update.to_id, PeerUser) and not update.out:
                portal = po.Portal.get_by_tgid(update.from_id, peer_type="user",
                                               tg_receiver=self.tgid)
            else:
                portal = po.Portal.get_by_entity(update.to_id, receiver_id=self.tgid)
            sender = pu.Puppet.get(update.from_id) if update.from_id else None
        else:
            self.log.warning(
                f"Unexpected message type in User#get_message_details: {type(update)}")
            return update, None, None
        return update, sender, portal

    @staticmethod
    async def _try_redact(portal, message):
        if not portal:
            return
        try:
            await portal.main_intent.redact(message.mx_room, message.mxid)
        except MatrixRequestError:
            pass

    async def delete_message(self, update):
        if len(update.messages) > MAX_DELETIONS:
            return

        for message in update.messages:
            message = DBMessage.query.get((message, self.tgid))
            if not message:
                continue
            self.db.delete(message)
            number_left = DBMessage.query.filter(DBMessage.mxid == message.mxid,
                                                 DBMessage.mx_room == message.mx_room).count()
            if number_left == 0:
                portal = po.Portal.get_by_mxid(message.mx_room)
                await self._try_redact(portal, message)
        self.db.commit()

    async def delete_channel_message(self, update):
        if len(update.messages) > MAX_DELETIONS:
            return

        portal = po.Portal.get_by_tgid(update.channel_id)
        if not portal:
            return

        for message in update.messages:
            message = DBMessage.query.get((message, portal.tgid))
            if not message:
                continue
            self.db.delete(message)
            await self._try_redact(portal, message)
        self.db.commit()

    async def update_message(self, original_update):
        update, sender, portal = self.get_message_details(original_update)

        if isinstance(update, MessageService):
            if isinstance(update.action, MessageActionChannelMigrateFrom):
                self.log.debug(f"Ignoring action %s to %s by %d", update.action,
                               portal.tgid_log,
                               sender.id)
                return
            self.log.debug("Handling action %s to %s by %d", update.action, portal.tgid_log,
                           sender.id)
            return await portal.handle_telegram_action(self, sender, update)

        user = sender.tgid if sender else "admin"
        if isinstance(original_update, (UpdateEditMessage, UpdateEditChannelMessage)):
            if config["bridge.edits_as_replies"]:
                self.log.debug("Handling edit %s to %s by %s", update, portal.tgid_log, user)
                return await portal.handle_telegram_edit(self, sender, update)
            return

        self.log.debug("Handling message %s to %s by %s", update, portal.tgid_log, user)
        return await portal.handle_telegram_message(self, sender, update)

    # endregion


def init(context):
    global config, MAX_DELETIONS
    AbstractUser.az, AbstractUser.db, config, AbstractUser.loop, _ = context
    AbstractUser.session_container = context.telethon_session_container
    MAX_DELETIONS = config.get("bridge.max_telegram_delete", 10)
