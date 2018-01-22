# mautrix-telegram
**This is the python rewrite branch and is only barely functional.**
**For a JavaScript version with more bugs and features, check the master branch.**

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
You can start private chats by simply inviting the Matrix puppet of the Telegram user you want to chat with to a private room.

If you don't know the MXID of the puppet, you can search for users using the `search <query>` management command.

#### Bot commands
Initiating chats with bots is no different from initiating chats with real Telegram users.

The bridge translates `!commands` into `/commands`, which allows you to use Telegram bots without constantly escaping
the slash. Please note that when messaging a bot for the first time, it may expect you to run `!start` first. The bridge
does not do this automatically.

## Features & Roadmap
* Matrix → Telegram
  * [x] Plaintext messages
  * [x] Formatted messages
    * [ ] Bot commands (!command -> /command)
    * [x] Mentions
  * [x] Rich quotes
  * [ ] Locations (not implemented in Riot)
  * [ ] Images
  * [ ] Files
  * [ ] Message redactions
  * [ ] Presence (currently always shown as online on Telegram)
  * [ ] Typing notifications (may not be possible)
  * [ ] Pinning messages
  * [ ] Power level
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
  * [ ] Images
  * [ ] Locations
  * [ ] Stickers
  * [ ] Audio messages
  * [ ] Video messages
  * [ ] Documents
  * [ ] Message deletions
  * [x] Presence
  * [x] Typing notifications
  * [ ] Pinning messages
  * [ ] Admin status
  * [ ] Membership actions
    * [ ] Inviting
    * [ ] Kicking
    * [ ] Joining/leaving
  * [x] Chat metadata changes
    * [ ] Public channel username changes
  * [x] Initial chat metadata
  * [ ] Supergroup upgrade
  * [ ] Message edits
* Initiating chats
  * [x] Automatic portal creation for groups/channels at startup
  * [ ] Automatic portal creation for groups/channels when receiving invite/message
  * [ ] Private chat creation by inviting Telegram user to new room
  * [ ] Joining public channels/supergroups using room aliases
  * [ ] Searching for Telegram users using management commands
  * [ ] Creating new Telegram chats from Matrix
  * [ ] Creating Telegram chats for existing Matrix rooms
* Misc
  * [ ] Use optional bot to relay messages for unauthenticated Matrix users
  * [ ] Command to upgrade chat to supergroup from Matrix
