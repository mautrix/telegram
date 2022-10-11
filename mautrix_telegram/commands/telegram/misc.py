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

from typing import cast
import base64
import codecs
import math
import re

from aiohttp import ClientSession, InvalidURL
from telethon.errors import (
    ChatIdInvalidError,
    EmoticonInvalidError,
    InviteHashExpiredError,
    InviteHashInvalidError,
    InviteRequestSentError,
    OptionsTooMuchError,
    TakeoutInitDelayError,
    UserAlreadyParticipantError,
)
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import (
    CheckChatInviteRequest,
    GetBotCallbackAnswerRequest,
    ImportChatInviteRequest,
    SendVoteRequest,
)
from telethon.tl.patched import Message
from telethon.tl.types import (
    InputMediaDice,
    MessageMediaGame,
    MessageMediaPoll,
    TypeInputPeer,
    TypeUpdates,
    User as TLUser,
)
from telethon.tl.types.messages import BotCallbackAnswer

from mautrix.types import EventID, Format

from ... import portal as po, puppet as pu
from ...abstract_user import AbstractUser
from ...commands import (
    SECTION_CREATING_PORTALS,
    SECTION_MISC,
    SECTION_PORTAL_MANAGEMENT,
    CommandEvent,
    command_handler,
)
from ...db import Message as DBMessage
from ...types import TelegramID


@command_handler(
    needs_auth=False,
    needs_puppeting=False,
    help_section=SECTION_MISC,
    help_args="<_caption_>",
    help_text="Set a caption for the next image you send",
)
async def caption(evt: CommandEvent) -> EventID:
    if len(evt.args) == 0:
        return await evt.reply("**Usage:** `$cmdprefix+sp caption <caption>`")

    prefix = f"{evt.command_prefix} caption "
    if evt.content.format == Format.HTML:
        evt.content.formatted_body = evt.content.formatted_body.replace(prefix, "", 1)
    evt.content.body = evt.content.body.replace(prefix, "", 1)
    evt.sender.command_status = {"caption": evt.content, "action": "Caption"}
    return await evt.reply(
        "Your next image or file will be sent with that caption. "
        "Use `$cmdprefix+sp cancel` to cancel the caption."
    )


@command_handler(
    help_section=SECTION_MISC,
    help_args="[_-r|--remote_] <_query_>",
    help_text="Search your contacts or the Telegram servers for users.",
)
async def search(evt: CommandEvent) -> EventID:
    if len(evt.args) == 0:
        return await evt.reply("**Usage:** `$cmdprefix+sp search [-r|--remote] <query>`")

    force_remote = False
    if evt.args[0] in {"-r", "--remote"}:
        force_remote = True
        evt.args.pop(0)

    query = " ".join(evt.args)
    if force_remote and len(query) < 5:
        return await evt.reply("Minimum length of query for remote search is 5 characters.")

    results, remote = await evt.sender.search(query, force_remote)

    if not results:
        if len(query) < 5 and remote:
            return await evt.reply(
                "No local results. Minimum length of remote query is 5 characters."
            )
        return await evt.reply("No results 3:")

    reply: list[str] = []
    if remote:
        reply += ["**Results from Telegram server:**", ""]
    else:
        reply += ["**Results in contacts:**", ""]
    reply += [
        (
            f"* [{puppet.displayname}](https://matrix.to/#/{puppet.mxid}): "
            f"{puppet.id} ({similarity}% match)"
        )
        for puppet, similarity in results
    ]

    # TODO somehow show remote channel results when joining by alias is possible?

    return await evt.reply("\n".join(reply))


@command_handler(
    help_section=SECTION_CREATING_PORTALS,
    help_args="<_identifier_>",
    help_text="Open a private chat with the given Telegram user. The identifier is "
    "either the internal user ID, the username or the phone number. "
    "**N.B.** The phone numbers you start chats with must already be in "
    "your contacts.",
)
async def pm(evt: CommandEvent) -> EventID:
    if len(evt.args) == 0:
        return await evt.reply("**Usage:** `$cmdprefix+sp pm <user identifier>`")

    try:
        id = "".join(evt.args).translate({ord(c): None for c in "+()- "})
        user = await evt.sender.client.get_entity(id)
    except ValueError:
        return await evt.reply("Invalid user identifier or user not found.")

    if not user:
        return await evt.reply("User not found.")
    elif not isinstance(user, TLUser):
        return await evt.reply("That doesn't seem to be a user.")
    portal = await po.Portal.get_by_entity(user, tg_receiver=evt.sender.tgid)
    await portal.create_matrix_room(evt.sender, user, [evt.sender.mxid])
    displayname, _ = pu.Puppet.get_displayname(user, False)
    return await evt.reply(f"Created private chat room with {displayname}")


async def _join(
    evt: CommandEvent, identifier: str, link_type: str
) -> tuple[TypeUpdates | None, EventID | None]:
    if link_type == "joinchat":
        try:
            await evt.sender.client(CheckChatInviteRequest(identifier))
        except InviteHashInvalidError:
            return None, await evt.reply("Invalid invite link.")
        except InviteHashExpiredError:
            return None, await evt.reply("Invite link expired.")
        try:
            return (await evt.sender.client(ImportChatInviteRequest(identifier))), None
        except UserAlreadyParticipantError:
            return None, await evt.reply("You are already in that chat.")
        except InviteRequestSentError:
            return None, await evt.reply("Invite request sent successfully.")
    else:
        channel = await evt.sender.client.get_entity(identifier)
        if not channel:
            return None, await evt.reply("Channel/supergroup not found.")
        return await evt.sender.client(JoinChannelRequest(channel)), None


@command_handler(
    help_section=SECTION_CREATING_PORTALS,
    help_args="<_link_>",
    help_text="Join a chat with an invite link.",
)
async def join(evt: CommandEvent) -> EventID | None:
    if len(evt.args) == 0:
        return await evt.reply("**Usage:** `$cmdprefix+sp join <invite link>`")

    url = evt.args[0]
    if evt.config["bridge.invite_link_resolve"]:
        try:
            async with ClientSession() as sess, sess.get(url) as resp:
                url = str(resp.url)
        except InvalidURL:
            return await evt.reply("That doesn't look like a Telegram invite link.")

    regex = re.compile(
        r"(?:https?://)?t(?:elegram)?\.(?:dog|me)(?:/(?P<type>joinchat|s))?/(?P<id>[^/]+)/?",
        flags=re.IGNORECASE,
    )
    arg = regex.match(url)
    if not arg:
        return await evt.reply("That doesn't look like a Telegram invite link.")

    data = arg.groupdict()
    identifier = data["id"]
    link_type = data["type"]
    if link_type:
        link_type = link_type.lower()
    elif identifier.startswith("+"):
        link_type = "joinchat"
        identifier = identifier[1:]
    updates, _ = await _join(evt, identifier, link_type)
    if not updates:
        return None

    for chat in updates.chats:
        portal = await po.Portal.get_by_entity(chat)
        if portal.mxid:
            await portal.invite_to_matrix([evt.sender.mxid])
            return await evt.reply(f"Invited you to portal of {portal.title}")
        else:
            await evt.reply(f"Creating room for {chat.title}... This might take a while.")
            try:
                await portal.create_matrix_room(evt.sender, chat, [evt.sender.mxid])
            except ChatIdInvalidError as e:
                evt.log.trace(
                    "ChatIdInvalidError while creating portal from !tg join command: %s",
                    updates.stringify(),
                )
                raise e
            if portal.mxid:
                return await evt.reply(f"Created room for {portal.title}")
            else:
                return await evt.reply(f"Couldn't create room for {portal.title}")
    return None


@command_handler(
    help_section=SECTION_MISC,
    help_args="[`chats`|`contacts`|`me`]",
    help_text="Synchronize your chat portals, contacts and/or own info.",
)
async def sync(evt: CommandEvent) -> EventID:
    if len(evt.args) > 0:
        sync_only = evt.args[0]
        if sync_only not in ("chats", "contacts", "me"):
            return await evt.reply("**Usage:** `$cmdprefix+sp sync [chats|contacts|me]`")
    else:
        sync_only = None

    if not sync_only or sync_only == "chats":
        await evt.reply("Synchronizing chats...")
        await evt.sender.sync_dialogs()
    if not sync_only or sync_only == "contacts":
        await evt.reply("Synchronizing contacts...")
        await evt.sender.sync_contacts()
    if not sync_only or sync_only == "me":
        await evt.sender.update_info()
    return await evt.reply("Synchronization complete.")


PEER_TYPE_CHAT = b"g"


class MessageIDError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


async def _parse_encoded_msgid(
    user: AbstractUser, enc_id: str, type_name: str
) -> tuple[TypeInputPeer, Message]:
    try:
        enc_id += (4 - len(enc_id) % 4) * "="
        enc_id = base64.b64decode(enc_id)
        peer_type, enc_id = bytes([enc_id[0]]), enc_id[1:]
        tgid = TelegramID(int(codecs.encode(enc_id[0:5], "hex_codec"), 16))
        msg_id = TelegramID(int(codecs.encode(enc_id[5:10], "hex_codec"), 16))
        space = None
        if peer_type == PEER_TYPE_CHAT:
            space = TelegramID(int(codecs.encode(enc_id[10:15], "hex_codec"), 16))
    except ValueError as e:
        raise MessageIDError(f"Invalid {type_name} ID (format)") from e

    if peer_type == PEER_TYPE_CHAT:
        orig_msg = await DBMessage.get_one_by_tgid(msg_id, space)
        if not orig_msg:
            raise MessageIDError(f"Invalid {type_name} ID (original message not found in db)")
        new_msg = await DBMessage.get_by_mxid(orig_msg.mxid, orig_msg.mx_room, user.tgid)
        if not new_msg:
            raise MessageIDError(f"Invalid {type_name} ID (your copy of message not found in db)")
        msg_id = new_msg.tgid
    try:
        peer = await user.client.get_input_entity(tgid)
    except ValueError as e:
        raise MessageIDError(f"Invalid {type_name} ID (chat not found)") from e

    msg = await user.client.get_messages(entity=peer, ids=msg_id)
    if not msg:
        raise MessageIDError(f"Invalid {type_name} ID (message not found)")
    return peer, cast(Message, msg)


@command_handler(
    help_section=SECTION_MISC, help_args="<_play ID_>", help_text="Play a Telegram game."
)
async def play(evt: CommandEvent) -> EventID:
    if len(evt.args) < 1:
        return await evt.reply("**Usage:** `$cmdprefix+sp play <play ID>`")
    elif not await evt.sender.is_logged_in():
        return await evt.reply("You must be logged in with a real account to play games.")
    elif evt.sender.is_bot:
        return await evt.reply("Bots can't play games :(")

    try:
        peer, msg = await _parse_encoded_msgid(evt.sender, evt.args[0], type_name="play")
    except MessageIDError as e:
        return await evt.reply(e.message)

    if not isinstance(msg.media, MessageMediaGame):
        return await evt.reply("Invalid play ID (message doesn't look like a game)")

    game = await evt.sender.client(
        GetBotCallbackAnswerRequest(peer=peer, msg_id=msg.id, game=True)
    )
    if not isinstance(game, BotCallbackAnswer):
        return await evt.reply("Game request response invalid")

    return await evt.reply(
        f"Click [here]({game.url}) to play {msg.media.game.title}:\n\n"
        f"{msg.media.game.description}"
    )


@command_handler(
    help_section=SECTION_MISC,
    help_args="<_poll ID_> <_choice number_>",
    help_text="Vote in a Telegram poll.",
)
async def vote(evt: CommandEvent) -> EventID | None:
    if len(evt.args) < 1:
        return await evt.reply("**Usage:** `$cmdprefix+sp vote <poll ID> <choice number>`")
    elif not await evt.sender.is_logged_in():
        return await evt.reply("You must be logged in with a real account to vote in polls.")
    elif evt.sender.is_bot:
        return await evt.reply("Bots can't vote in polls :(")

    try:
        peer, msg = await _parse_encoded_msgid(evt.sender, evt.args[0], type_name="poll")
    except MessageIDError as e:
        return await evt.reply(e.message)

    if not isinstance(msg.media, MessageMediaPoll):
        return await evt.reply("Invalid poll ID (message doesn't look like a poll)")

    options = []
    for option in evt.args[1:]:
        try:
            if len(option) > 10:
                raise ValueError("option index too long")
            option_index = int(option) - 1
        except ValueError:
            option_index = None
        if option_index is None:
            return await evt.reply(
                f'Invalid option number "{option}"', render_markdown=False, allow_html=False
            )
        elif option_index < 0:
            return await evt.reply(
                f"Invalid option number {option}. Option numbers must be positive."
            )
        elif option_index >= len(msg.media.poll.answers):
            return await evt.reply(
                f"Invalid option number {option}. "
                f"The poll only has {len(msg.media.poll.answers)} options."
            )
        options.append(msg.media.poll.answers[option_index].option)
    options = [msg.media.poll.answers[int(option) - 1].option for option in evt.args[1:]]
    try:
        await evt.sender.client(SendVoteRequest(peer=peer, msg_id=msg.id, options=options))
    except OptionsTooMuchError:
        return await evt.reply("You passed too many options.")
    # TODO use response
    return await evt.mark_read()


@command_handler(
    help_section=SECTION_MISC,
    help_args="<_emoji_>",
    help_text="Roll a dice (\U0001F3B2), kick a football (\u26BD\uFE0F) or throw a "
    "dart (\U0001F3AF) or basketball (\U0001F3C0) on the Telegram servers.",
)
async def random(evt: CommandEvent) -> EventID:
    if not evt.is_portal:
        return await evt.reply("You can only randomize values in portal rooms")
    portal = await po.Portal.get_by_mxid(evt.room_id)
    arg = evt.args[0] if len(evt.args) > 0 else "dice"
    emoticon = {
        "dart": "\U0001F3AF",
        "dice": "\U0001F3B2",
        "ball": "\U0001F3C0",
        "basketball": "\U0001F3C0",
        "football": "\u26BD",
        "soccer": "\u26BD",
    }.get(arg, arg)
    try:
        await evt.sender.client.send_media(
            await portal.get_input_entity(evt.sender), InputMediaDice(emoticon)
        )
    except EmoticonInvalidError:
        return await evt.reply("Invalid emoji for randomization")


@command_handler(
    help_section=SECTION_PORTAL_MANAGEMENT,
    help_args="[_limit_]",
    help_text="Backfill messages from Telegram history.",
)
async def backfill(evt: CommandEvent) -> None:
    if not evt.is_portal:
        await evt.reply("You can only use backfill in portal rooms")
        return
    elif not evt.config["bridge.backfill.enable"]:
        await evt.reply("Backfilling is disabled in the bridge config")
        return
    try:
        limit = int(evt.args[0])
    except (ValueError, IndexError):
        limit = -1
    portal = await po.Portal.get_by_mxid(evt.room_id)
    if not evt.config["bridge.backfill.normal_groups"] and portal.peer_type == "chat":
        await evt.reply("Backfilling normal groups is disabled in the bridge config")
        return
    if portal.backfill_msc2716:
        messages_per_batch = evt.config["bridge.backfill.incremental.messages_per_batch"]
        batches = math.ceil(limit / messages_per_batch)
        rounded = ""
        if batches * messages_per_batch != limit:
            rounded = f" (rounded message limit to {batches}*{messages_per_batch})"
        await portal.enqueue_backfill(evt.sender, priority=0, max_batches=batches)
        await evt.reply(f"Backfill queued{rounded}")
    else:
        output = await portal.forward_backfill(evt.sender, initial=False, override_limit=limit)
        await evt.reply(output)
