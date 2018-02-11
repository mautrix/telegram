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
import markdown
import logging
import asyncio

from mautrix_appservice import MatrixRequestError

from telethon.errors import *
from telethon.tl.types import *
from telethon.tl.functions.messages import ImportChatInviteRequest, CheckChatInviteRequest
from telethon.tl.functions.channels import JoinChannelRequest

from . import puppet as pu, portal as po

command_handlers = {}


def command_handler(func):
    command_handlers[func.__name__] = func
    return func


def format_duration(seconds):
    def pluralize(count, singular): return singular if count == 1 else singular + "s"

    def include(count, word): return f"{count} {pluralize(count, word)}" if count > 0 else ""

    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts = [a for a in [
        include(days, "day"),
        include(hours, "hour"),
        include(minutes, "minute"),
        include(seconds, "second")] if a]
    if len(parts) > 2:
        return "{} and {}".format(", ".join(parts[:-1]), parts[-1])
    return " and ".join(parts)


class CommandEvent:
    def __init__(self, az, command_prefix, room, sender, args, is_management, is_portal):
        self.az = az
        self.command_prefix = command_prefix
        self.room_id = room
        self.sender = sender
        self.args = args
        self.is_management = is_management
        self.is_portal = is_portal

    def reply(self, message, allow_html=False, render_markdown=True):
        if not self.room_id:
            raise AttributeError("the reply function can only be used from within"
                                 "the `CommandHandler.run` context manager")

        message = message.replace("$cmdprefix+sp ",
                                  "" if self.is_management else f"{self.command_prefix} ")
        message = message.replace("$cmdprefix", self.command_prefix)
        html = None
        if render_markdown:
            html = markdown.markdown(message, safe_mode="escape" if allow_html else False)
        elif allow_html:
            html = message
        return self.az.intent.send_notice(self.room_id, message, html=html)


class CommandHandler:
    log = logging.getLogger("mau.commands")

    def __init__(self, context):
        self.az, self.db, self.config, self.loop = context
        self.command_prefix = self.config["bridge.command_prefix"]

    # region Utility functions for handling commands

    async def handle(self, room, sender, command, args, is_management, is_portal):
        evt = CommandEvent(self.az, self.command_prefix, room, sender, args, is_management,
                           is_portal)
        command = command.lower()
        try:
            command = command_handlers[command]
        except KeyError:
            if sender.command_status and "next" in sender.command_status:
                args.insert(0, command)
                command = sender.command_status["next"]
            else:
                command = command_handlers["unknown_command"]
        try:
            await command(self, evt)
        except FloodWaitError as e:
            return evt.reply(f"Flood error: Please wait {format_duration(e.seconds)}")
        except Exception:
            self.log.exception(f"Fatal error handling command "
                               + f"'$cmdprefix {command} {''.join(args)}' from {sender.mxid}")
            return evt.reply("Fatal error while handling command. Check logs for more details.")

    # endregion
    # region Command handlers

    @command_handler
    async def ping(self, evt):
        if not evt.sender.logged_in:
            return await evt.reply("You're not logged in.")
        me = await evt.sender.client.get_me()
        if me:
            return await evt.reply(f"You're logged in as @{me.username}")
        else:
            return await evt.reply("You're not logged in.")

    # region Authentication commands
    @command_handler
    def register(self, evt):
        return evt.reply("Not yet implemented.")

    @command_handler
    async def login(self, evt):
        if not evt.is_management:
            return await evt.reply(
                "`login` is a restricted command: you may only run it in management rooms.")
        elif evt.sender.logged_in:
            return await evt.reply("You are already logged in.")
        elif len(evt.args) == 0:
            return await evt.reply("**Usage:** `$cmdprefix+sp login <phone number>`")
        phone_number = evt.args[0]
        await evt.sender.client.sign_in(phone_number)
        evt.sender.command_status = {
            "next": command_handlers["enter_code"],
            "action": "Login",
        }
        return await evt.reply(f"Login code sent to {phone_number}. Please send the code here.")

    @command_handler
    async def enter_code(self, evt):
        if not evt.sender.command_status:
            return await evt.reply(
                "Request a login code first with `$cmdprefix+sp login <phone>`")
        elif len(evt.args) == 0:
            return await evt.reply("**Usage:** `$cmdprefix+sp enter_code <code>`")

        try:
            user = await evt.sender.client.sign_in(code=evt.args[0])
            asyncio.ensure_future(evt.sender.post_login(user), loop=self.loop)
            evt.sender.command_status = None
            return await evt.reply(f"Successfully logged in as @{user.username}")
        except PhoneNumberUnoccupiedError:
            return await evt.reply("That phone number has not been registered."
                                   "Please register with `$cmdprefix+sp register <phone>`.")
        except PhoneCodeExpiredError:
            return await evt.reply(
                "Phone code expired. Try again with `$cmdprefix+sp login <phone>`.")
        except PhoneCodeInvalidError:
            return await evt.reply("Invalid phone code.")
        except PhoneNumberAppSignupForbiddenError:
            return await evt.reply(
                "Your phone number does not allow 3rd party apps to sign in.")
        except PhoneNumberFloodError:
            return await evt.reply(
                "Your phone number has been temporarily blocked for flooding. "
                "The block is usually applied for around a day.")
        except PhoneNumberBannedError:
            return await evt.reply("Your phone number has been banned from Telegram.")
        except SessionPasswordNeededError:
            evt.sender.command_status = {
                "next": command_handlers["enter_password"],
                "action": "Login (password entry)",
            }
            return await evt.reply("Your account has two-factor authentication."
                                   "Please send your password here.")
        except Exception:
            self.log.exception("Error sending phone code")
            return await evt.reply("Unhandled exception while sending code."
                                   "Check console for more details.")

    @command_handler
    async def enter_password(self, evt):
        if not evt.sender.command_status:
            return await evt.reply(
                "Request a login code first with `$cmdprefix+sp login <phone>`")
        elif len(evt.args) == 0:
            return await evt.reply("**Usage:** `$cmdprefix+sp enter_password <password>`")

        try:
            user = await evt.sender.client.sign_in(password=evt.args[0])
            asyncio.ensure_future(evt.sender.post_login(user), loop=self.loop)
            evt.sender.command_status = None
            return await evt.reply(f"Successfully logged in as @{user.username}")
        except PasswordHashInvalidError:
            return await evt.reply("Incorrect password.")
        except Exception:
            self.log.exception("Error sending password")
            return await evt.reply("Unhandled exception while sending password. "
                                   "Check console for more details.")

    @command_handler
    async def logout(self, evt):
        if not evt.sender.logged_in:
            return await evt.reply("You're not logged in.")
        if await evt.sender.log_out():
            return await evt.reply("Logged out successfully.")
        return await evt.reply("Failed to log out.")

    # endregion
    # region Telegram interaction commands

    @command_handler
    async def search(self, evt):
        if len(evt.args) == 0:
            return await evt.reply("**Usage:** `$cmdprefix+sp search [-r|--remote] <query>`")
        elif not evt.sender.logged_in:
            return await evt.reply("This command requires you to be logged in.")

        force_remote = False
        if evt.args[0] in {"-r", "--remote"}:
            force_remote = True
            evt.args.pop(0)

        query = " ".join(evt.args)
        if force_remote and len(query) < 5:
            return await evt.reply("Minimum length of query for remote search is 5 characters.")

        results, remote = await evt.sender.search(query, force_remote)

        reply = []
        if remote:
            reply += ["**Results from Telegram server:**", ""]
        else:
            reply += ["**Results in contacts:**", ""]
        reply += [(f"* [{puppet.displayname}](https://matrix.to/#/{puppet.mxid}): "
                   + f"{puppet.id} ({similarity}% match)")
                  for puppet, similarity in results]

        # TODO somehow show remote channel results when joining by alias is possible?

        return await evt.reply("\n".join(reply))

    @command_handler
    async def pm(self, evt):
        if len(evt.args) == 0:
            return await evt.reply("**Usage:** `$cmdprefix+sp pm <user identifier>`")
        elif not evt.sender.logged_in:
            return await evt.reply("This command requires you to be logged in.")

        user = await evt.sender.client.get_entity(evt.args[0])
        if not user:
            return await evt.reply("User not found.")
        elif not isinstance(user, User):
            return await evt.reply("That doesn't seem to be a user.")
        portal = po.Portal.get_by_entity(user, evt.sender.tgid)
        await portal.create_matrix_room(evt.sender, user, [evt.sender.mxid])
        return await evt.reply(
            f"Created private chat room with {pu.Puppet.get_displayname(user, False)}")

    @command_handler
    async def invitelink(self, evt):
        if not evt.sender.logged_in:
            return await evt.reply("This command requires you to be logged in.")

        portal = po.Portal.get_by_mxid(evt.room_id)
        if not portal:
            return await evt.reply("This is not a portal room.")

        if portal.peer_type == "user":
            return await evt.reply("You can't invite users to private chats.")

        try:
            link = await portal.get_invite_link(evt.sender)
            return await evt.reply(f"Invite link to {portal.title}: {link}")
        except ValueError as e:
            return await evt.reply(e.args[0])
        except ChatAdminRequiredError:
            return await evt.reply("You don't have the permission to create an invite link.")

    @command_handler
    async def deleteportal(self, evt):
        if not evt.sender.logged_in:
            return await evt.reply("This command requires you to be logged in.")
        elif not evt.sender.is_admin:
            return await evt.reply("This is command requires administrator privileges.")

        portal = po.Portal.get_by_mxid(evt.room_id)
        if not portal:
            return await evt.reply("This is not a portal room.")

        for user in await portal.main_intent.get_room_members(portal.mxid):
            if user != portal.main_intent.mxid:
                try:
                    await portal.main_intent.kick(portal.mxid, user, "Portal deleted.")
                except MatrixRequestError:
                    pass
        await portal.main_intent.leave_room(portal.mxid)
        portal.delete()

    @staticmethod
    def _strip_prefix(value, prefixes):
        for prefix in prefixes:
            if value.startswith(prefix):
                return value[len(prefix):]
        return value

    @command_handler
    async def join(self, evt):
        if len(evt.args) == 0:
            return await evt.reply("**Usage:** `$cmdprefix+sp join <invite link>`")
        elif not evt.sender.logged_in:
            return await evt.reply("This command requires you to be logged in.")

        regex = re.compile(r"(?:https?://)?t(?:elegram)?\.(?:dog|me)(?:joinchat/)?/(.+)")
        arg = regex.match(evt.args[0])
        if not arg:
            return await evt.reply("That doesn't look like a Telegram invite link.")
        arg = arg.group(1)
        if arg.startswith("joinchat/"):
            invite_hash = arg[len("joinchat/"):]
            try:
                await evt.sender.client(CheckChatInviteRequest(invite_hash))
            except InviteHashInvalidError:
                return await evt.reply("Invalid invite link.")
            except InviteHashExpiredError:
                return await evt.reply("Invite link expired.")
            try:
                updates = evt.sender.client(ImportChatInviteRequest(invite_hash))
            except UserAlreadyParticipantError:
                return await evt.reply("You are already in that chat.")
        else:
            channel = await evt.sender.client.get_entity(arg)
            if not channel:
                return await evt.reply("Channel/supergroup not found.")
            updates = await evt.sender.client(JoinChannelRequest(channel))
        for chat in updates.chats:
            portal = po.Portal.get_by_entity(chat)
            if portal.mxid:
                await portal.create_matrix_room(evt.sender, chat, [evt.sender.mxid])
                return await evt.reply(f"Created room for {portal.title}")
            else:
                await portal.invite_matrix([evt.sender.mxid])
                return await evt.reply(f"Invited you to portal of {portal.title}")

    @command_handler
    async def create(self, evt):
        type = evt.args[0] if len(evt.args) > 0 else "group"
        if type not in {"chat", "group", "supergroup", "channel"}:
            return await evt.reply(
                "**Usage:** `$cmdprefix+sp create ['group'/'supergroup'/'channel']`")
        elif not evt.sender.logged_in:
            return await evt.reply("This command requires you to be logged in.")

        if po.Portal.get_by_mxid(evt.room_id):
            return await evt.reply("This is already a portal room.")

        state = await self.az.intent.get_room_state(evt.room_id)
        title = None
        about = None
        levels = None
        for event in state:
            if event["type"] == "m.room.name":
                title = event["content"]["name"]
            elif event["type"] == "m.room.topic":
                about = event["content"]["topic"]
            elif event["type"] == "m.room.power_levels":
                levels = event["content"]
        if not title:
            return await evt.reply("Please set a title before creating a Telegram chat.")
        elif (not levels or not levels["users"] or self.az.intent.mxid not in levels["users"] or
              levels["users"][self.az.intent.mxid] < 100):
            return await evt.reply(f"Please give "
                                   + f"[the bridge bot](https://matrix.to/#/{self.az.intent.mxid})"
                                   + f" a power level of 100 before creating a Telegram chat.")
        else:
            for user, level in levels["users"].items():
                if level >= 100 and user != self.az.intent.mxid:
                    return await evt.reply(
                        f"Please make sure only the bridge bot has power level above"
                        + f"99 before creating a Telegram chat.\n\n"
                        + f"Use power level 95 instead of 100 for admins.")

        supergroup = type == "supergroup"
        type = {
            "supergroup": "channel",
            "channel": "channel",
            "chat": "chat",
            "group": "chat",
        }[type]

        portal = po.Portal(tgid=None, mxid=evt.room_id, title=title, about=about, peer_type=type)
        try:
            await portal.create_telegram_chat(evt.sender, supergroup=supergroup)
        except ValueError as e:
            return await evt.reply(e.args[0])
        return await evt.reply(f"Telegram chat created. ID: {portal.tgid}")

    @command_handler
    async def upgrade(self, evt):
        if not evt.sender.logged_in:
            return await evt.reply("This command requires you to be logged in.")

        portal = po.Portal.get_by_mxid(evt.room_id)
        if not portal:
            return await evt.reply("This is not a portal room.")
        elif portal.peer_type == "channel":
            return await evt.reply("This is already a supergroup or a channel.")
        elif portal.peer_type == "user":
            return await evt.reply("You can't upgrade private chats.")

        try:
            await portal.upgrade_telegram_chat(evt.sender)
            return await evt.reply(f"Group upgraded to supergroup. New ID: {portal.tgid}")
        except ChatAdminRequiredError:
            return await evt.reply("You don't have the permission to upgrade this group.")
        except ValueError as e:
            return await evt.reply(e.args[0])

    @command_handler
    async def groupname(self, evt):
        if len(evt.args) == 0:
            return await evt.reply("**Usage:** `$cmdprefix+sp groupname <name/->`")
        if not evt.sender.logged_in:
            return await evt.reply("This command requires you to be logged in.")

        portal = po.Portal.get_by_mxid(evt.room_id)
        if not portal:
            return await evt.reply("This is not a portal room.")
        elif portal.peer_type != "channel":
            return await evt.reply("Only channels and supergroups have usernames.")

        try:
            await portal.set_telegram_username(evt.sender,
                                               evt.args[0] if evt.args[0] != "-" else "")
            if portal.username:
                return await evt.reply(f"Username of channel changed to {portal.username}.")
            else:
                return await evt.reply(f"Channel is now private.")
        except ChatAdminRequiredError:
            return await evt.reply(
                "You don't have the permission to set the username of this channel.")
        except UsernameNotModifiedError:
            if portal.username:
                return await evt.reply("That is already the username of this channel.")
            else:
                return await evt.reply("This channel is already private")
        except UsernameOccupiedError:
            return await evt.reply("That username is already in use.")
        except UsernameInvalidError:
            return await evt.reply("Invalid username")

    # endregion
    # region Command-related commands
    @command_handler
    def cancel(self, evt):
        if evt.sender.command_status:
            action = evt.sender.command_status["action"]
            evt.sender.command_status = None
            return evt.reply(f"{action} cancelled.")
        else:
            return evt.reply("No ongoing command.")

    @command_handler
    def unknown_command(self, evt):
        return evt.reply("Unknown command. Try `$cmdprefix+sp help` for help.")

    @command_handler
    def help(self, evt):
        if evt.is_management:
            management_status = ("This is a management room: prefixing commands "
                                 "with `$cmdprefix` is not required.\n")
        elif evt.is_portal:
            management_status = ("**This is a portal room**: you must always "
                                 "prefix commands with `$cmdprefix`.\n"
                                 "Management commands will not be sent to Telegram.")
        else:
            management_status = ("**This is not a management room**: you must "
                                 "prefix commands with `$cmdprefix`.\n")
        help = """\n
#### Generic bridge commands
**help**   - Show this help message.  
**cancel** - Cancel an ongoing action (such as login).

#### Authentication
**login** <_phone_> - Request an authentication code.  
**logout**          - Log out from Telegram.  
**ping**            - Check if you're logged into Telegram.

#### Initiating chats
**search** [_-r|--remote_] <_query_> - Search your contacts or the Telegram servers for users.  
**pm** <_identifier_>                - Open a private chat with the given Telegram user. The
                                       identifier is either the internal user ID, the username or
                                       the phone number.  
**join** <_link_>                    - Join a chat with an invite link.  
**create** [_type_]                  - Create a Telegram chat of the given type for the current
                                       Matrix room. The type is either `group`, `supergroup` or
                                       `channel` (defaults to `group`).

#### Portal management  
**upgrade**                - Upgrade a normal Telegram group to a supergroup.  
**invitelink**             - Get a Telegram invite link to the current chat.  
**deleteportal**           - Forget the current portal room. Only works for group chats; to delete
                             a private chat portal, simply leave the room.  
**groupname** <_name_|`-`> - Change the username of a supergroup/channel. To disable, use a dash
                             (`-`) as the name.
"""
        return evt.reply(management_status + help)

    # endregion
    # endregion
