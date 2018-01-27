# mautrix-telegram
**This is the python rewrite. The JavaScript version on the master branch has different features and more bugs**

A Matrix-Telegram puppeting bridge.

## Discussion
Matrix room: [`#telegram:maunium.net`](https://matrix.to/#/#telegram:maunium.net)

A Telegram chat will be created once the bridge is stable enough.

## Usage
### Setup
0. Clone the repository
1. Set up the virtual environment
   1. Create with `virtualenv -p /usr/bin/python3 .venv`
   2. Activate with `source .venv/bin/activate`
2. Install dependencies with `pip install -r requirements.txt`
3. Create a copy of `example-config.yaml` and fill out the fields.
4. Generate the appservice registration with `python -m mautrix_telegram -g`.
   You can use the `-c` and `-r` flags to change the location of the config and registration files.
   They default to `config.yaml` and `registration.yaml` respectively.
5. Run the bridge `python -m mautrix_telegram`.
6. Invite the appservice bot to a private room and view the commands with `help`.

### Logging in
0. Make sure you have set up the bridge and have an open management room (a room with no other users than the appservice bot).
1. Request a Telegram auth code with `login <phone number>`.
2. Send your auth code to the management room.
3. If you have two-factor authentication enabled, send your password to the room.
4. If all prior steps were executed successfully, the bridge should now create rooms for all your Telegram dialogs and invite you to them.

### Chatting
#### Group chats and channels
You should be automatically invited into portal rooms for your groups and channels if you
1. (re)start the bridge,
2. receive a messages in the chat or
3. receive an invite to the chat

Support for inviting users both Telegram and Matrix users to Telegram portal rooms is planned, but not yet implemented.

#### Private messaging
**Initiating private chats is not yet implemented.** In order to initiate a private chat,
send a message in either direction with another Telegram client.

~~You can start private chats by simply inviting the Matrix puppet of the Telegram user you want to chat with to a private room.~~

~~If you don't know the MXID of the puppet, you can search for users using the `search <query>` management command.~~

#### Bot commands
**Initiating private chats is not yet implemented.** In order to initiate a chat with a,
bot, send a message to the bot with another Telegram client.

~~Initiating chats with bots is no different from initiating chats with real Telegram users.~~

~~The bridge translates `!commands` into `/commands`, which allows you to use Telegram bots without constantly escaping
the slash. Please note that when messaging a bot for the first time, it may expect you to run `!start` first. The bridge
does not do this automatically.~~

## Features & Roadmap
* Matrix → Telegram
  * [x] Plaintext messages
  * [x] Formatted messages
    * [ ] Bot commands (!command -> /command)
    * [x] Mentions
  * [x] Rich quotes
  * [ ] Locations (not implemented in Riot)
  * [x] Images
  * [x] Files
  * [x] Message redactions
  * [ ] Presence (currently always shown as online on Telegram)
  * [ ] Typing notifications (may not be possible)
  * [ ] Pinning messages
  * [x] Power level
  * [ ] Membership actions
    * [ ] Inviting
    * [ ] Kicking
    * [ ] Joining/leaving
  * [ ] Room metadata changes
  * [ ] Room invites
* Telegram → Matrix
  * [x] Plaintext messages
  * [x] Formatted messages
    * [x] Bot commands (/command -> !command)
    * [x] Mentions
  * [x] Replies
  * [x] Forwards
  * [x] Images
  * [x] Locations
  * [x] Stickers
  * [x] Audio messages
  * [x] Video messages
  * [x] Documents
  * [ ] Message deletions (no way to tell difference between user-specific deletion and global deletion)
  * [ ] Message edits (not supported in Matrix)
  * [x] Avatars
  * [x] Presence
  * [x] Typing notifications
  * [ ] Pinning messages
  * [x] Admin/chat creator status
  * [x] Membership actions
    * [x] Inviting
    * [x] Kicking
    * [x] Joining/leaving
  * [x] Chat metadata changes
    * [ ] Public channel username changes
  * [x] Initial chat metadata
  * [x] Supergroup upgrade
* Initiating chats
  * [x] Automatic portal creation for groups/channels at startup
  * [x] Automatic portal creation for groups/channels when receiving invite/message
  * [ ] Private chat creation by inviting Telegram user to new room
  * [ ] Searching for Telegram users using management commands
* Misc
  * [ ] Use optional bot to relay messages for unauthenticated Matrix users
  * [ ] Joining public channels/supergroups using room aliases
* Commands
  * [x] Logging in and out (`login` + code entering, `logout`)
  * [ ] Registering (`register`)
  * [ ] Searching for users (`search`)
  * [ ] Starting private chats (`pm`)
  * [ ] Creating a Telegram chat for an existing Matrix room (`create`)
  * [ ] Upgrading the chat of a portal room into a supergroup (`upgrade`)
