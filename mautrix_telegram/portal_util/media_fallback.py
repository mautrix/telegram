# mautrix-telegram - A Matrix-Telegram puppeting bridge
# Copyright (C) 2021 Tulir Asokan
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
from __future__ import annotations

import html

from telethon.tl.types import MessageMediaContact, MessageMediaDice, PeerUser

from mautrix.types import Format, MessageType, TextMessageEventContent

from .. import abstract_user as au, puppet as pu
from ..types import TelegramID

try:
    import phonenumbers
except ImportError:
    phonenumbers = None


def _format_dice(roll: MessageMediaDice) -> str:
    if roll.emoticon == "\U0001F3B0":
        emojis = {
            0: "\U0001F36B",  # "🍫",
            1: "\U0001F352",  # "🍒",
            2: "\U0001F34B",  # "🍋",
            3: "7\ufe0f\u20e3",  # "7️⃣",
        }
        res = roll.value - 1
        slot1, slot2, slot3 = emojis[res % 4], emojis[res // 4 % 4], emojis[res // 16]
        return f"{slot1} {slot2} {slot3} ({roll.value})"
    elif roll.emoticon == "\u26BD":
        results = {
            1: "miss",
            2: "hit the woodwork",
            3: "goal",  # seems to go in through the center
            4: "goal",
            5: "goal 🎉",  # seems to go in through the top right corner, includes confetti
        }
    elif roll.emoticon == "\U0001F3B3":
        results = {
            1: "miss",
            2: "1 pin down",
            3: "3 pins down, split",
            4: "4 pins down, split",
            5: "5 pins down",
            6: "strike 🎉",
        }
    # elif roll.emoticon == "\U0001F3C0":
    #     results = {
    #         2: "rolled off",
    #         3: "stuck",
    #     }
    # elif roll.emoticon == "\U0001F3AF":
    #     results = {
    #         1: "bounced off",
    #         2: "outer rim",
    #
    #         6: "bullseye",
    #     }
    else:
        return str(roll.value)
    return f"{results[roll.value]} ({roll.value})"


def make_dice_event_content(roll: MessageMediaDice) -> TextMessageEventContent:
    emoji_text = {
        "\U0001F3AF": " Dart throw",
        "\U0001F3B2": " Dice roll",
        "\U0001F3C0": " Basketball throw",
        "\U0001F3B0": " Slot machine",
        "\U0001F3B3": " Bowling",
        "\u26BD": " Football kick",
    }
    text = f"{roll.emoticon}{emoji_text.get(roll.emoticon, '')} result: {_format_dice(roll)}"
    content = TextMessageEventContent(
        msgtype=MessageType.TEXT, format=Format.HTML, body=text, formatted_body=f"<h4>{text}</h4>"
    )
    content["net.maunium.telegram.dice"] = {"emoticon": roll.emoticon, "value": roll.value}
    return content


async def make_contact_event_content(
    source: au.AbstractUser, contact: MessageMediaContact
) -> TextMessageEventContent:
    name = " ".join(x for x in [contact.first_name, contact.last_name] if x)
    formatted_phone = f"+{contact.phone_number}"
    if phonenumbers is not None:
        try:
            parsed = phonenumbers.parse(formatted_phone)
            fmt = phonenumbers.PhoneNumberFormat.INTERNATIONAL
            formatted_phone = phonenumbers.format_number(parsed, fmt)
        except phonenumbers.NumberParseException:
            pass
    content = TextMessageEventContent(
        msgtype=MessageType.TEXT,
        body=f"Shared contact info for {name}: {formatted_phone}",
    )
    content["net.maunium.telegram.contact"] = {
        "user_id": contact.user_id,
        "first_name": contact.first_name,
        "last_name": contact.last_name,
        "phone_number": contact.phone_number,
        "vcard": contact.vcard,
    }

    puppet = await pu.Puppet.get_by_tgid(TelegramID(contact.user_id))
    if not puppet.displayname:
        try:
            entity = await source.client.get_entity(PeerUser(contact.user_id))
            await puppet.update_info(source, entity)
        except Exception as e:
            source.log.warning(f"Failed to sync puppet info of received contact: {e}")
    else:
        content.format = Format.HTML
        content.formatted_body = (
            f"Shared contact info for "
            f"<a href='https://matrix.to/#/{puppet.mxid}'>{html.escape(name)}</a>: "
            f"{html.escape(formatted_phone)}"
        )
    return content
