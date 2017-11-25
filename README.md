# mautrix-telegram
**Work in progress: Expect bugs, do not use in production.**

A Matrix-Telegram puppeting bridge.

## Discussion
Matrix room: [`#telegram:maunium.net`](https://matrix.to/#/#telegram:maunium.net)

A Telegram chat will be created once the bridge is stable enough.

## Usage
### Setup
0. Clone the repository and install packages with `npm install`. Also, you'll probably need to run `npm run fix-auth-renewal` to roll back telegram-mtproto to a version where server salt renewal works ([zerobias/telegram-mtproto#99](https://github.com/zerobias/telegram-mtproto/issues/99))
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
