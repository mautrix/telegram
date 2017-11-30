# mautrix-telegram
**Work in progress: Expect bugs, do not use in production.**

A Matrix-Telegram puppeting bridge.

## Discussion
Matrix room: [`#telegram:maunium.net`](https://matrix.to/#/#telegram:maunium.net)

A Telegram chat will be created once the bridge is stable enough.

## Usage
### Setup
0. Clone the repository and install packages with `npm install`.
1. Create a copy of `example-config.yaml` and fill out the fields.
2. Generate the appservice registration with `./mautrix-telegram -g`.
   You can use the `-c` and `-r` flags to change the location of the config and registration files.
   They default to `config.yaml` and `registration.yaml` respectively.
3. Run the bridge `./mautrix-telegram`. You can also use forever: `forever start mautrix-telegram` (probably, I didn't actually test it).
4. Invite the appservice bot to a private room and view the commands with `help`.

### Logging in
0. Make sure you have set up the bridge and have an open management room (a room with no other users than the appservice bot).
1. Request a Telegram auth code with `login <phone number>`.
2. Send your auth code to the management room.
3. If you have two-factor authentication enabled, send your password to the room.
4. If all prior steps were executed successfully, the bridge should now create rooms for all your Telegram dialogs and invite you to them.

## Features & Roadmap
* Matrix → Telegram
  * [x] Plaintext messages
  * [x] Formatted messages
  * [ ] Images
  * [ ] Files
  * [ ] Message redactions
  * [ ] Presence (currently always shown as online on Telegram)
  * [ ] Typing notifications (may not be possible)
  * [ ] Membership actions (invite, kick, join, leave)
  * [ ] Pinning messages
  * [ ] Power level
* Telegram → Matrix
  * [x] Plaintext messages
  * [x] Formatted messages
  * [x] Images
  * [ ] Stickers (somewhat works through document upload, no preview though)
  * [x] Audio messages
  * [ ] Video messages
  * [x] Documents
  * [x] Locations
  * [x] Presence
  * [x] Typing notifications
  * [ ] Pinning messages
  * [ ] Message edits
  * [ ] Message deletions
  * [ ] Admin status
  * [x] Initial group/channel name/description
  * [x] Membership actions (invite, kick, join, leave)
  * [ ] Group/channel name/description changes
* Initiating chats
  * [x] Automatic portal creation for groups/channels at startup
  * [x] Automatic portal creation for groups/channels when receiving invite/message
  * [x] Private chat creation by inviting Telegram user to new room
  * [ ] Joining public channels/supergroups using room aliases
  * [x] Searching for Telegram users using management commands
* Misc
  * [ ] Use optional bot to relay messages for unauthenticated Matrix users
  * [ ] Properly handle upgrading groups to supergroups
  * [ ] Creating new Telegram groups from Matrix
  * [ ] Creating Telegram groups for existing Matrix rooms
