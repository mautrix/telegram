# mautrix-telegram
A Matrix-Telegram puppeting bridge.

## Discussion
Matrix room: [`#telegram:maunium.net`](https://matrix.to/#/#telegram:maunium.net)

A Telegram chat will be created once the bridge is stable enough.

## Usage
### Setup
0. Clone the repository
1. Set up the virtual environment
   1. Create with `virtualenv -p /usr/bin/python3 .venv` (requires Python 3.5 or higher)
   2. Activate with `source .venv/bin/activate`
2. Install dependencies with `pip install -r requirements.txt`
3. Copy `example-config.yaml` to `config.yaml` and fill out the fields.
4. Generate the appservice registration with `python -m mautrix_telegram -g`.
   You can use the `-c` and `-r` flags to change the location of the config and registration files.
   They default to `config.yaml` and `registration.yaml` respectively.
5. Run the bridge `python -m mautrix_telegram`.
6. Invite the appservice bot to a private room and view the commands with `help`.

### Logging in
0. Make sure you have set up the bridge and have an open management room (a room with no other
   users than the appservice bot).
1. Request a Telegram auth code with `login <phone number>`.
2. Send your auth code to the management room.
3. If you have two-factor authentication enabled, send your password to the room.
4. If all prior steps were executed successfully, the bridge should now create rooms for all your
   Telegram groups and channels and invite you to them.

### Chatting
#### Group chats and channels
You should be automatically invited into portal rooms for your groups and channels if you
1. (re)start the bridge,
2. receive a messages in the chat or
3. receive an invite to the chat

Inviting Telegram puppets to rooms should work. However, please don't invite non-puppet Matrix
users to portal rooms yet.

You can also create a Telegram chat for an existing Matrix room using `!tg create` in the room.
However, there are some restrictions:
* The room must have a title.
* The AS bot must be invited first (before puppets) and be given power level 100.
* The AS bot must be the only user to have power level 100.

#### Private messaging
You can start private chats by simply inviting the Matrix puppet of the Telegram user you want to chat with to a private room.

If you don't know the MXID of the puppet, you can search for users using the `search <query>` management command.

You can also initiate chats with the `pm` command using the username, phone number or user ID.

#### Bot commands
Initiating chats with bots is no different from initiating chats with real Telegram users.

~~The bridge translates `!commands` into `/commands`, which allows you to use Telegram bots without constantly escaping
the slash.~~ Please note that when messaging a bot for the first time, it may expect you to run ~~`!start`~~ `/start` first.
The bridge does not do this automatically.

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
  * [ ] † Presence
  * [ ] † Typing notifications
  * [ ] Pinning messages
  * [ ] Power level
    * [x] Normal chats
	* [ ] Supergroups/channels (currently only creator level bridged)
  * [ ] Membership actions
    * [x] Inviting puppets
    * [ ] Inviting Matrix users who have logged in to Telegram
    * [x] Kicking
    * [ ] Joining (once room aliases have been implemented)
    * [x] Leaving
  * [x] Room metadata changes (name, topic, avatar)
  * [x] Initial room metadata
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
  * [ ] Supergroup/channel permissions
  * [x] Membership actions
    * [x] Inviting
    * [x] Kicking
    * [x] Joining/leaving
  * [ ] Chat metadata changes
    * [x] Title
    * [x] Avatar
    * [ ] † About text
    * [ ] † Public channel username
  * [x] Initial chat metadata (about text missing)
  * [x] Supergroup upgrade
* Misc
  * [x] Automatic portal creation
    * [x] At startup
    * [x] When receiving invite or message
  * [x] Private chat creation by inviting Matrix puppet of Telegram user to new room
  * [ ] Option to use bot to relay messages for unauthenticated Matrix users
  * [ ] Option to use own Matrix account for messages sent from other Telegram clients
  * [ ] Joining chats with room aliases
    * [ ] Name of public channel/supergroup as alias
	* [ ] (Maybe) Invite link token as alias
* Commands
  * [x] Logging in and out (`login` + code entering, `logout`)
  * [ ] Registering (`register`)
  * [x] Searching for users (`search`)
    * [ ] Searching contacts locally
  * [x] Starting private chats (`pm`)
  * [x] Joining chats with invite links (`join`)
  * [x] Creating a Telegram chat for an existing Matrix room (`create`)
  * [ ] Upgrading the chat of a portal room into a supergroup (`upgrade`)
  * [ ] Change public/private status of supergroup/channel (`setpublic`)
  * [ ] Change username of supergroup/channel (`groupname`)
  * [x] Getting the Telegram invite link to a Matrix room (`invitelink`)
  * [x] Clean up and forget a portal room (`deleteportal`)

† Information not automatically sent from source, i.e. implementation may not be possible
