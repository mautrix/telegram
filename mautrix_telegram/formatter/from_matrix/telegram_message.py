# -*- coding: future_fstrings -*-
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
from typing import Callable, List, Optional, Sequence, Type, Union

from telethon.tl.types import (MessageEntityMentionName as MentionName,
                               MessageEntityTextUrl as TextURL, MessageEntityPre as Pre,
                               TypeMessageEntity, InputMessageEntityMentionName as InputMentionName)


class Entity:
    @staticmethod
    def copy(entity: TypeMessageEntity) -> Optional[TypeMessageEntity]:
        if not entity:
            return None
        kwargs = {
            "offset": entity.offset,
            "length": entity.length,
        }
        if isinstance(entity, Pre):
            kwargs["language"] = entity.language
        elif isinstance(entity, TextURL):
            kwargs["url"] = entity.url
        elif isinstance(entity, (MentionName, InputMentionName)):
            kwargs["user_id"] = entity.user_id
        return entity.__class__(**kwargs)

    @classmethod
    def adjust(cls, entity: Union[TypeMessageEntity, List[TypeMessageEntity]],
               func: Callable[[TypeMessageEntity], None]
               ) -> Union[Optional[TypeMessageEntity], List[TypeMessageEntity]]:
        if isinstance(entity, list):
            return [Entity.adjust(element, func) for element in entity if entity]
        elif not entity:
            return None
        entity = cls.copy(entity)
        func(entity)
        if entity.offset < 0:
            entity.length += entity.offset
            entity.offset = 0
        return entity


def offset_diff(amount: int) -> Callable[[TypeMessageEntity], None]:
    def func(entity: TypeMessageEntity) -> None:
        entity.offset += amount

    return func


def offset_length_multiply(amount: int) -> Callable[[TypeMessageEntity], None]:
    def func(entity: TypeMessageEntity) -> None:
        entity.offset *= amount
        entity.length *= amount

    return func


class TelegramMessage:
    def __init__(self, text: str = "", entities: Optional[List[TypeMessageEntity]] = None) -> None:
        self.text = text  # type: str
        self.entities = entities or []  # type: List[TypeMessageEntity]

    def offset_entities(self, offset: int) -> 'TelegramMessage':
        def apply_offset(entity: TypeMessageEntity, inner_offset: int
                         ) -> Optional[TypeMessageEntity]:
            entity = Entity.copy(entity)
            entity.offset += inner_offset
            if entity.offset < 0:
                entity.offset = 0
            elif entity.offset > len(self.text):
                return None
            elif entity.offset + entity.length > len(self.text):
                entity.length = len(self.text) - entity.offset
            return entity

        self.entities = [apply_offset(entity, offset) for entity in self.entities if entity]
        self.entities = [x for x in self.entities if x is not None]
        return self

    def append(self, *args: Union[str, 'TelegramMessage']) -> 'TelegramMessage':
        for msg in args:
            if isinstance(msg, str):
                msg = TelegramMessage(text=msg)
            self.entities += Entity.adjust(msg.entities, offset_diff(len(self.text)))
            self.text += msg.text
        return self

    def prepend(self, *args: Union[str, 'TelegramMessage']) -> 'TelegramMessage':
        for msg in args:
            if isinstance(msg, str):
                msg = TelegramMessage(text=msg)
            self.entities = msg.entities + Entity.adjust(self.entities, offset_diff(len(msg.text)))
            self.text = msg.text + self.text
        return self

    def format(self, entity_type: Type[TypeMessageEntity], offset: int = None, length: int = None,
               **kwargs) -> 'TelegramMessage':
        self.entities.append(entity_type(offset=offset or 0,
                                         length=length if length is not None else len(self.text),
                                         **kwargs))
        return self

    def concat(self, *args: Union[str, 'TelegramMessage']) -> 'TelegramMessage':
        return TelegramMessage().append(self, *args)

    def trim(self) -> 'TelegramMessage':
        orig_len = len(self.text)
        self.text = self.text.lstrip()
        diff = orig_len - len(self.text)
        self.text = self.text.rstrip()
        self.offset_entities(-diff)
        return self

    def split(self, separator, max_items: int = 0) -> List['TelegramMessage']:
        text_parts = self.text.split(separator, max_items - 1)
        output = []  # type: List[TelegramMessage]

        offset = 0
        for part in text_parts:
            msg = TelegramMessage(part)
            for entity in self.entities:
                start_in_range = len(part) > entity.offset - offset >= 0
                end_in_range = len(part) >= entity.offset - offset + entity.length > 0
                if start_in_range and end_in_range:
                    msg.entities.append(Entity.adjust(entity, offset_diff(-offset)))
            output.append(msg)

            offset += len(part)
            offset += len(separator)

        return output

    @staticmethod
    def join(items: Sequence[Union[str, 'TelegramMessage']],
             separator: str = " ") -> 'TelegramMessage':
        main = TelegramMessage()
        for msg in items:
            if isinstance(msg, str):
                msg = TelegramMessage(text=msg)
            main.entities += Entity.adjust(msg.entities, offset_diff(len(main.text)))
            main.text += msg.text + separator
        if len(separator) > 0:
            main.text = main.text[:-len(separator)]
        return main
