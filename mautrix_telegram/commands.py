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
from contextlib import contextmanager
import markdown
from matrix_client.errors import MatrixRequestError
from telethon.errors import *
from telethon.tl.types import *
from telethon.tl.functions.contacts import SearchRequest
from telethon.tl.functions.messages import ImportChatInviteRequest, CheckChatInviteRequest
from telethon.tl.functions.channels import JoinChannelRequest
from . import puppet as pu, portal as po

command_handlers = {}


def command_handler(func):
    command_handlers[func.__name__] = func
    return func


class CommandHandler:
    def __init__(self, context):
        self.az, self.db, log, self.config = context
        self.log = log.getChild("commands")
        self.command_prefix = self.config["bridge.command_prefix"]
        self._room_id = None
        self._is_management = False
        self._is_portal = False

    # region Utility functions for handling commands

    def handle(self, room, sender, command, args, is_management, is_portal):
        with self.handler(sender, room, command, args, is_management, is_portal) as handle_command:
            try:
                handle_command(self, sender, args)
            except Exception:
                self.reply("Fatal error while handling command. Check logs for more details.")
                self.log.exception(f"Fatal error handling command "
                                   + f"'$cmdprefix {command} {''.join(args)}' from {sender.mxid}")

    @contextmanager
    def handler(self, sender, room, command, args, is_management, is_portal):
        command = command.lower()
        self._room_id = room
        try:
            command = command_handlers[command]
        except KeyError:
            if sender.command_status and "next" in sender.command_status:
                args.insert(0, command)
                command = sender.command_status["next"]
            else:
                command = command_handlers["unknown_command"]
        self._is_management = is_management
        self._is_portal = is_portal
        yield command
        self._is_management = None
        self._is_portal = None
        self._room_id = None

    def reply(self, message, allow_html=False, render_markdown=True):
        if not self._room_id:
            raise AttributeError("the reply function can only be used from within"
                                 "the `CommandHandler.run` context manager")

        message = message.replace("$cmdprefix+sp ",
                                  "" if self._is_management else f"{self.command_prefix} ")
        message = message.replace("$cmdprefix", self.command_prefix)
        html = None
        if render_markdown:
            html = markdown.markdown(message, safe_mode="escape" if allow_html else False)
        elif allow_html:
            html = message
        self.az.intent.send_notice(self._room_id, message, html=html)

    # endregion
    # region Command handlers

    @command_handler
    def ping(self, sender, args):
        if not sender.logged_in:
            return self.reply("You're not logged in.")
        me = sender.client.get_me()
        if me:
            return self.reply(f"You're logged in as @{me.username}")
        else:
            return self.reply("You're not logged in.")

    # region Authentication commands
    @command_handler
    def register(self, sender, args):
        self.reply("Not yet implemented.")

    @command_handler
    def login(self, sender, args):
        if not self._is_management:
            return self.reply(
                "`login` is a restricted command: you may only run it in management rooms.")
        elif sender.logged_in:
            return self.reply("You are already logged in.")
        elif len(args) == 0:
            return self.reply("**Usage:** `$cmdprefix+sp login <phone number>`")
        phone_number = args[0]
        sender.client.send_code_request(phone_number)
        sender.client.sign_in(phone_number)
        sender.command_status = {
            "next": command_handlers["enter_code"],
            "action": "Login",
        }
        return self.reply(f"Login code sent to {phone_number}. Please send the code here.")

    @command_handler
    def enter_code(self, sender, args):
        if not sender.command_status:
            return self.reply("Request a login code first with `$cmdprefix+sp login <phone>`")
        elif len(args) == 0:
            return self.reply("**Usage:** `$cmdprefix+sp enter_code <code>")

        try:
            user = sender.client.sign_in(code=args[0])
            sender.post_login(user)
            sender.command_status = None
            return self.reply(f"Successfully logged in as @{user.username}")
        except PhoneNumberUnoccupiedError:
            return self.reply("That phone number has not been registered."
                              "Please register with `$cmdprefix+sp register <phone>`.")
        except PhoneCodeExpiredError:
            return self.reply(
                "Phone code expired. Try again with `$cmdprefix+sp login <phone>`.")
        except PhoneCodeInvalidError:
            return self.reply("Invalid phone code.")
        except PhoneNumberAppSignupForbiddenError:
            return self.reply(
                "Your phone number does not allow 3rd party apps to sign in.")
        except PhoneNumberFloodError:
            return self.reply(
                "Your phone number has been temporarily blocked for flooding. "
                "The block is usually applied for around a day.")
        except PhoneNumberBannedError:
            return self.reply("Your phone number has been banned from Telegram.")
        except SessionPasswordNeededError:
            sender.command_status = {
                "next": command_handlers["enter_password"],
                "action": "Login (password entry)",
            }
            return self.reply("Your account has two-factor authentication."
                              "Please send your password here.")
        except Exception:
            self.log.exception()
            return self.reply("Unhandled exception while sending code."
                              "Check console for more details.")

    @command_handler
    def enter_password(self, sender, args):
        if not sender.command_status:
            return self.reply("Request a login code first with `$cmdprefix+sp login <phone>`")
        elif len(args) == 0:
            return self.reply("**Usage:** `$cmdprefix+sp enter_password <password>")

        try:
            user = sender.client.sign_in(password=args[0])
            sender.post_login(user)
            sender.command_status = None
            return self.reply(f"Successfully logged in as @{user.username}")
        except PasswordHashInvalidError:
            return self.reply("Incorrect password.")
        except Exception:
            self.log.exception()
            return self.reply("Unhandled exception while sending password. "
                              "Check console for more details.")

    @command_handler
    def logout(self, sender, args):
        if not sender.logged_in:
            return self.reply("You're not logged in.")
        if sender.log_out():
            return self.reply("Logged out successfully.")
        return self.reply("Failed to log out.")

    # endregion
    # region Telegram interaction commands

    @command_handler
    def search(self, sender, args):
        if len(args) == 0:
            return self.reply("**Usage:** `$cmdprefix+sp search [-r|--remote] <query>")
        elif not sender.logged_in:
            return self.reply("This command requires you to be logged in.")
        # force_remote = False
        if args[0] in {"-r", "--remote"}:
            # force_remote = True
            args.pop(0)
        query = " ".join(args)
        if len(query) < 5:
            return self.reply("Minimum length of query for remote search is 5 characters.")
        found = sender.client(SearchRequest(q=query, limit=10))

        # reply = ["**People:**", ""]
        reply = ["**Results from Telegram server:**", ""]
        for result in found.users:
            puppet = pu.Puppet.get(result.id)
            puppet.update_info(sender, result)
            reply.append(
                f"* [{puppet.displayname}](https://matrix.to/#/{puppet.mxid}): {puppet.id}")
        # reply.extend(("", "**Chats:**", ""))
        # for result in found.chats:
        #     reply.append(f"* {result.title}")
        return self.reply("\n".join(reply))

    @command_handler
    def pm(self, sender, args):
        if len(args) == 0:
            return self.reply("**Usage:** `$cmdprefix+sp pm <user identifier>`")
        elif not sender.logged_in:
            return self.reply("This command requires you to be logged in.")

        user = sender.client.get_entity(args[0])
        if not user:
            return self.reply("User not found.")
        elif not isinstance(user, User):
            return self.reply("That doesn't seem to be a user.")
        portal = po.Portal.get_by_entity(user, sender.tgid)
        portal.create_matrix_room(sender, user, [sender.mxid])
        self.reply(f"Created private chat room with {pu.Puppet.get_displayname(user, False)}")

    @command_handler
    def invitelink(self, sender, args):
        if not sender.logged_in:
            return self.reply("This command requires you to be logged in.")

        portal = po.Portal.get_by_mxid(self._room_id)
        if not portal:
            return self.reply("This is not a portal room.")

        if portal.peer_type == "user":
            return self.reply("You can't invite users to private chats.")

        try:
            link = portal.get_invite_link(sender)
            return self.reply(f"Invite link to {portal.title}: {link}")
        except ValueError as e:
            return self.reply(e.args[0])
        except ChatAdminRequiredError:
            return self.reply("You don't have the permission to create an invite link.")

    @command_handler
    def deleteportal(self, sender, args):
        if not sender.logged_in:
            return self.reply("This command requires you to be logged in.")
        elif not sender.is_admin:
            return self.reply("This is command requires administrator privileges.")

        portal = po.Portal.get_by_mxid(self._room_id)
        if not portal:
            return self.reply("This is not a portal room.")

        for user in portal.main_intent.get_room_members(portal.mxid):
            if user != portal.main_intent.mxid:
                try:
                    portal.main_intent.kick(portal.mxid, user, "Portal deleted.")
                except MatrixRequestError:
                    pass
        portal.main_intent.leave_room(portal.mxid)
        portal.delete()

    @staticmethod
    def _strip_prefix(value, prefixes):
        for prefix in prefixes:
            if value.startswith(prefix):
                return value[len(prefix):]
        return value

    @command_handler
    def join(self, sender, args):
        if len(args) == 0:
            return self.reply("**Usage:** `$cmdprefix+sp join <invite link>")
        elif not sender.logged_in:
            return self.reply("This command requires you to be logged in.")

        regex = re.compile(r"(?:https?://)?t(?:elegram)?\.(?:dog|me)(?:joinchat/)?/(.+)")
        arg = regex.match(args[0])
        if not arg:
            return self.reply("That doesn't look like a Telegram invite link.")
        arg = arg.group(1)
        if arg.startswith("joinchat/"):
            invite_hash = arg[len("joinchat/"):]
            try:
                sender.client(CheckChatInviteRequest(invite_hash))
            except InviteHashInvalidError:
                return self.reply("Invalid invite link.")
            except InviteHashExpiredError:
                return self.reply("Invite link expired.")
            try:
                updates = sender.client(ImportChatInviteRequest(invite_hash))
            except UserAlreadyParticipantError:
                return self.reply("You are already in that chat.")
        else:
            channel = sender.client.get_entity(arg)
            if not channel:
                return self.reply("Channel/supergroup not found.")
            updates = sender.client(JoinChannelRequest(channel))
        for chat in updates.chats:
            portal = po.Portal.get_by_entity(chat)
            portal.create_matrix_room(sender, chat, [sender.mxid])
            self.reply(f"Created room for {portal.title}")

    @command_handler
    def create(self, sender, args):
        type = args[0] if len(args) > 0 else "group"
        if type not in {"chat", "group", "supergroup", "channel"}:
            return self.reply("**Usage:** `$cmdprefix+sp create [`group`/`supergroup`/`channel`]")
        elif not sender.logged_in:
            return self.reply("This command requires you to be logged in.")

        if po.Portal.get_by_mxid(self._room_id):
            return self.reply("This is already a portal room.")

        state = self.az.intent.get_room_state(self._room_id)
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
            return self.reply("Please set a title before creating a Telegram chat.")
        elif (not levels or not levels["users"] or self.az.intent.mxid not in levels["users"] or
              levels["users"][self.az.intent.mxid] < 100):
            return self.reply(f"Please give "
                              + f"[the bridge bot](https://matrix.to/#/{self.az.intent.mxid}) "
                              + f"a power level of 100 before creating a Telegram chat.")
        else:
            for user, level in levels["users"].items():
                if level >= 100 and user != self.az.intent.mxid:
                    return self.reply(f"Please make sure only the bridge bot has power level above"
                                      + f"99 before creating a Telegram chat.\n\n"
                                      + f"Use power level 95 instead of 100 for admins.")

        supergroup = type == "supergroup"
        type = {
            "supergroup": "channel",
            "channel": "channel",
            "chat": "chat",
            "group": "chat",
        }[type]

        portal = po.Portal(tgid=None, mxid=self._room_id, title=title, about=about, peer_type=type)
        try:
            portal.create_telegram_chat(sender, supergroup=supergroup)
        except ValueError as e:
            return self.reply(e.args[0])
        self.reply(f"Telegram chat created. ID: {portal.tgid}")

    @command_handler
    def upgrade(self, sender, args):
        if not sender.logged_in:
            return self.reply("This command requires you to be logged in.")

        portal = po.Portal.get_by_mxid(self._room_id)
        if not portal:
            return self.reply("This is not a portal room.")
        elif portal.peer_type == "channel":
            return self.reply("This is already a supergroup or a channel.")
        elif portal.peer_type == "user":
            return self.reply("You can't upgrade private chats.")

        try:
            portal.upgrade_telegram_chat(sender)
            return self.reply(f"Group upgraded to supergroup. New ID: {portal.tgid}")
        except ChatAdminRequiredError:
            return self.reply("You don't have the permission to upgrade this group.")
        except ValueError as e:
            return self.reply(e.args[0])

    @command_handler
    def setpublic(self, sender, args):
        self.reply("Not yet implemented.")

    @command_handler
    def groupname(self, sender, args):
        self.reply("Not yet implemented.")

    # endregion
    # region Command-related commands
    @command_handler
    def cancel(self, sender, args):
        if sender.command_status:
            action = sender.command_status["action"]
            sender.command_status = None
            return self.reply(f"{action} cancelled.")
        else:
            return self.reply("No ongoing command.")

    @command_handler
    def unknown_command(self, sender, args):
        if self._is_management:
            return self.reply("Unknown command. Try `help` for help.")
        else:
            return self.reply("Unknown command. Try `$cmdprefix help` for help.")

    @command_handler
    def help(self, sender, args):
        if self._is_management:
            management_status = ("This is a management room: prefixing commands "
                                 "with `$cmdprefix` is not required.\n")
        elif self._is_portal:
            management_status = ("**This is a portal room**: you must always "
                                 "prefix commands with `$cmdprefix`.\n"
                                 "Management commands will not be sent to Telegram.")
        else:
            management_status = ("**This is not a management room**: you must "
                                 "prefix commands with `$cmdprefix`.\n")
        help = """\n
#### Generic bridge commands
**help** - Show this help message.  
**cancel** - Cancel an ongoing action (such as login).

#### Authentication
**login** <_phone_> - Request an authentication code.  
**logout** - Log out from Telegram.  
**ping** - Check if you're logged into Telegram.

#### Initiating chats
**search** [_-r|--remote_] <_query_> - Search your contacts or the Telegram servers for users.  
**pm** <_identifier_> - Open a private chat with the given Telegram user. The identifier is either
                        the internal user ID, the username or the phone number.  
**join** <_link_> - Join a chat with an invite link.  
**create** [_type_] - Create a Telegram chat of the given type for the current Matrix room.
                      The type is either `group`, `supergroup` or `channel` (defaults to `group`).

#### Portal management  
† **upgrade** - Upgrade a normal Telegram group to a supergroup.  
**invitelink** - Get a Telegram invite link to the current chat.  
**deleteportal** - Forget the current portal room. Only works for group chats; to delete a private
                   chat portal, simply leave the room.  
† **setpublic** <_yes/no_> - Change whether or not a supergroup/channel is public.  
† **groupname** <_name_> - Change the username of a supergroup/channel.
                         To disable, use `setpublic no`.


† Not yet implemented
"""
        return self.reply(management_status + help)

    # endregion
    # endregion
