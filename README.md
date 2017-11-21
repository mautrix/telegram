# mautrix-telegram
**Work in progress: Expect bugs, do not use in production.**

A Matrix-Telegram puppeting bridge.

## Usage
### Setup
1. Create a copy of `example-config.yaml` and fill out the fields.
2. Generate the appservice registration with `./mautrix-telegram -g`.
   You can use the `-c` and `-r` flags to change the location of the config and registration files.
   They default to `config.yaml` and `registration.yaml` respectively.
3. Run the bridge `./mautrix-telegram`. You can also use forever: `forever start mautrix-telegram` (probably, I didn't actually test it).
4. Invite the appservice bot to a private room and view the commands with `!tg help`.

### Logging in
1. Request a Telegram auth code with `!tg login <phone number>`.
2. Send your auth code with `!tg <auth code>`.
3. If you have two-factor authentication enabled, send your password with `!tg <password>`
4. If all prior steps were executed successfully, the bridge should now create rooms for all your Telegram dialogs and invite you to them.
