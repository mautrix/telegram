# mautrix-telegram
**Not yet functional.**

A Matrix-Telegram puppeting bridge.

## Usage
1. Create a copy of `example-config.yaml` and fill out the fields.
2. Generate the appservice registration with `./mautrix-telegram -g`.
   You can use the `-c` and `-r` flags to change the location of the config and registration files.
   They default to `config.yaml` and `registration.yaml` respectively.
3. Run the bridge `./mautrix-telegram`. You can also use forever: `forever start mautrix-telegram`.
4. Invite the appservice bot to a private room and view the commands with `!help`.
