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
from io import BytesIO

from telethon import TelegramClient
from telethon.tl.functions.messages import SendMessageRequest, SendMediaRequest
from telethon.tl.types import *


class MautrixTelegramClient(TelegramClient):
    def send_message(self, entity, message, reply_to=None, entities=None, link_preview=True):
        entity = self.get_input_entity(entity)

        request = SendMessageRequest(
            peer=entity,
            message=message,
            entities=entities,
            no_webpage=not link_preview,
            reply_to_msg_id=self._get_reply_to(reply_to)
        )
        result = self(request)
        if isinstance(result, UpdateShortSentMessage):
            return Message(
                id=result.id,
                to_id=entity,
                message=message,
                date=result.date,
                out=result.out,
                media=result.media,
                entities=result.entities
            )

        return self._get_response_message(request, result)

    def send_file(self, entity, file, mime_type=None, caption=None, attributes=None, file_name=None,
                  reply_to=None, **kwargs):
        entity = self.get_input_entity(entity)
        reply_to = self._get_reply_to(reply_to)

        file_handle = self.upload_file(file, file_name=file_name, use_cache=False)

        if mime_type == "image/png":
            media = InputMediaUploadedPhoto(file_handle, caption or "")
        else:
            attributes = attributes or []
            attr_dict = {type(attr): attr for attr in attributes}

            media = InputMediaUploadedDocument(
                file=file_handle,
                mime_type=mime_type or "application/octet-stream",
                attributes=list(attr_dict.values()),
                caption=caption or "")

        request = SendMediaRequest(entity, media, reply_to_msg_id=reply_to)
        return self._get_response_message(request, self(request))

    def download_file_bytes(self, location):
        if isinstance(location, Document):
            location = InputDocumentFileLocation(location.id, location.access_hash,
                                                 location.version)
        elif not isinstance(location, (InputFileLocation, InputDocumentFileLocation)):
            location = InputFileLocation(location.volume_id, location.local_id, location.secret)

        file = BytesIO()

        self.download_file(location, file)

        data = file.getvalue()
        file.close()
        return data
