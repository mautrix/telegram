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
from io import BytesIO

from telethon import TelegramClient
from telethon.tl.functions.messages import SendMessageRequest, SendMediaRequest
from telethon.tl.types import *
from telethon.extensions.markdown import parse as parse_md


class MautrixTelegramClient(TelegramClient):
    async def send_message(self, entity, message, reply_to=None, entities=None, markdown=False,
                           link_preview=True):
        entity = await self.get_input_entity(entity)

        if markdown:
            message, entities = parse_md(message)

        request = SendMessageRequest(
            peer=entity,
            message=message,
            entities=entities,
            no_webpage=not link_preview,
            reply_to_msg_id=self._get_message_id(reply_to)
        )
        result = await self(request)
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

    async def upload_file(self, file, mime_type=None, attributes=None, file_name=None):
        file_handle = await super().upload_file(file, file_name=file_name, use_cache=False)

        if mime_type == "image/png" or mime_type == "image/jpeg":
            return InputMediaUploadedPhoto(file_handle)
        else:
            attributes = attributes or []
            attr_dict = {type(attr): attr for attr in attributes}

            return InputMediaUploadedDocument(
                file=file_handle,
                mime_type=mime_type or "application/octet-stream",
                attributes=list(attr_dict.values()))

    async def send_media(self, entity, media, caption=None, entities=None, reply_to=None):
        entity = await self.get_input_entity(entity)
        reply_to = self._get_message_id(reply_to)
        request = SendMediaRequest(entity, media, message=caption or "", entities=entities or [],
                                   reply_to_msg_id=reply_to)
        return self._get_response_message(request, await self(request))

    async def download_file_bytes(self, location):
        if isinstance(location, Document):
            location = InputDocumentFileLocation(location.id, location.access_hash,
                                                 location.version)
        elif not isinstance(location, (InputFileLocation, InputDocumentFileLocation)):
            location = InputFileLocation(location.volume_id, location.local_id, location.secret)

        file = BytesIO()

        await self.download_file(location, file)

        data = file.getvalue()
        file.close()
        return data
