# v0.12.1 (unreleased)

### Added
* Support for custom emojis in reactions.
  * Like other bridges with custom emoji reactions, they're bridged as `mxc://`
    URIs, so client support is required to render them properly.

### Improved
* The bridge will now poll for reactions to 20 most recent messages when
  receiving a read receipt. This works around Telegram's bad protocol that
  doesn't notify clients on reactions to other users' messages.
* The docker image now has an option to bypass the startup script by setting
  the `MAUTRIX_DIRECT_STARTUP` environment variable. Additionally, it will
  refuse to run as a non-root user if that variable is not set (and print an
  error message suggesting to either set the variable or use a custom command).
* Moved environment variable overrides for config fields to mautrix-python.
  The new system also allows loading JSON values to enable overriding maps like
  `login_shared_secret_map`.

### Fixed
* `ChatParticipantsForbidden` is handled properly when syncing non-supergroup
  info.

# v0.12.0 (2022-08-26)

**N.B.** This release requires a homeserver with Matrix v1.1 support, which
bumps up the minimum homeserver versions to Synapse 1.54 and Dendrite 0.8.7.
Minimum Conduit version remains at 0.4.0.

### Added
* Added provisioning API for resolving Telegram identifiers (like usernames).
* Added support for bridging Telegram custom emojis to Matrix.
* Added option to not bridge chats with lots of members.
* Added option to include captions in the same message as the media to
  implement [MSC2530]. Sending captions the same way is also supported and
  enabled by default.
* Added commands to kick or ban relaybot users from Telegram.
* Added support for Telegram's disappearing messages.
* Added support for bridging forwarded messages as forwards on Telegram.
  * Forwarding is not allowed in relay mode as the bot wouldn't be able to
    specify who sent the message.
  * Matrix doesn't have real forwarding (there's no forwarding metadata), so
    only messages bridged from Telegram can be forwarded.
  * Double puppeted messages from Telegram currently can't be forwarded without
    removing the `fi.mau.double_puppet_source` key from the content.
  * If forwarding fails (e.g. due to it being blocked in the source chat), the
    bridge will automatically fall back to sending it as a normal new message.
* Added options to make encryption more secure.
  * The `encryption` -> `verification_levels` config options can be used to
    make the bridge require encrypted messages to come from cross-signed
    devices, with trust-on-first-use validation of the cross-signing master
    key.
  * The `encryption` -> `require` option can be used to make the bridge ignore
    any unencrypted messages.
  * Key rotation settings can be configured with the `encryption` -> `rotation`
    config.

### Improved
* Improved handling the bridge user leaving chats on Telegram, and new users
  being added on Telegram.
* Improved animated sticker conversion options: added support for animated webp
  and added option to convert video stickers (webm) to the specified image
  format.
* Audio and video metadata is now bridged properly to Telegram.
* Added database index on Telegram usernames (used when bridging username
  @-mentions in messages).
* Changed `/login/send_code` provisioning API to return a proper error when the
  phone number is not registered on Telegram.
  * The same login code can be used for registering an account, but registering
    is not currently supported in the provisioning API.
* Removed `plaintext_highlights` config option (the code using it was already
  removed in v0.11.0).
* Enabled appservice ephemeral events by default for new installations.
  * Existing bridges can turn it on by enabling `ephemeral_events` and disabling
    `sync_with_custom_puppets` in the config, then regenerating the registration
    file.
* Updated to API layer 144 so that Telegram would send new message types like
  premium stickers to the bridge.
* Updated Docker image to Alpine 3.16 and made it smaller.

### Fixed
* Fixed command prefix in game and poll messages (thanks to [@cynhr] in [#804]).

[MSC2530]: https://github.com/matrix-org/matrix-spec-proposals/pull/2530
[@cynhr]: https://github.com/cynhr
[#804]: https://github.com/mautrix/telegram/pull/804

# v0.11.3 (2022-04-17)

**N.B.** This release drops support for old homeservers which don't support the
new `/v3` API endpoints. Synapse 1.48+, Dendrite 0.6.5+ and Conduit 0.4.0+ are
supported. Legacy `r0` API support can be temporarily re-enabled with `pip install mautrix==0.16.0`.
However, this option will not be available in future releases.

### Added
* Added `list-invite-links` command to list invite links in a chat.
* Added option to use [MSC2246] async media uploads.
* Provisioning API for listing contacts and starting private chats.

### Improved
* Dropped Python 3.7 support.
* Telegram->Matrix message formatter will now replace `t.me/c/chatid/messageid`
  style links with a link to the bridged Matrix event (in addition to the
  previously supported `t.me/username/messageid` links).
* Updated formatting converter to keep newlines in code blocks as `\n` instead
  of converting them to `<br/>`.
* Removed `max_document_size` option. The bridge will now fetch the max size
  automatically using the media repo config endpoint.
* Removed redundant `msgtype` field in sticker events sent to Matrix.
* Disabled file logging in Docker image by default.
  * If you want to enable it, set the `filename` in the file log handler to a
    path that is writable, then add `"file"` back to `logging.root.handlers`.
* Reactions are now marked as read when bridging read receipts from Matrix.

### Fixed
* Fixed `!tg bridge` throwing error if the parameter is not an integer
* Fixed `!tg bridge` failing if the command had been previously run with an
  incorrectly prefixed chat ID (e.g. `!tg bridge -1234567` followed by
  `!tg bridge -1001234567`).
* Fixed `bridge_matrix_leave` config option not actually being used correctly.
* Fixed public channel mentions always bridging into a user mention on Matrix
  rather than a room mention.
  * The bridge will now make room mentions if the portal exists and fall back
    to user mentions otherwise.
* Fixed newlines being lost in unformatted forwarded messages.

[MSC2246]: https://github.com/matrix-org/matrix-spec-proposals/pull/2246

# v0.11.2 (2022-02-14)

**N.B.** This will be the last release to support Python 3.7. Future versions
will require Python 3.8 or higher. In general, the mautrix bridges will only
support the lowest Python version in the latest Debian or Ubuntu LTS.

### Added
* Added simple fallback message for live location and venue messages from Telegram.
* Added support for `t.me/+code` style invite links in `!tg join`.
* Added support for showing channel profile when users send messages as a channel.
* Added "user joined Telegram" message when Telegram auto-creates a DM chat for
  a new user.

### Improved
* Added option for adding a random prefix to relayed user displaynames to help
  distinguish them on the Telegram side.
* Improved syncing profile info to room info when using encryption and/or the
  `private_chat_profile_meta` config option.
* Removed legacy `community_id` config option.

### Fixed
* Fixed newlines disappearing when bridging channel messages with signatures.
* Fixed login throwing an error if a previous login code expired.
* Fixed bug in v0.11.0 that broke `!tg create`.

# v0.11.1 (2022-01-10)

### Added
* Added support for message reactions.
* Added support for spoiler text.

### Improved
* Support for voice messages.
* Changed color of blue text from Telegram to be more readable on dark themes.

### Fixed
* Fixed syncing contacts throwing an error for new accounts.
* Fixed migrating pre-v0.11 legacy databases if the database schema had been
  corrupted (e.g. by using 3rd party tools for SQLite -> Postgres migration).
* Fixed converting animated stickers to webm with >33 FPS.
* Fixed a bug in v0.11.0 that broke mentioning users in groups
  (thanks to [@dfuchss] in [#724]).

[@dfuchss]: https://github.com/dfuchss
[#724]: https://github.com/mautrix/telegram/pull/724

# v0.11.0 (2021-12-28)

* Switched from SQLAlchemy to asyncpg/aiosqlite.
  * The default database is now Postgres. If using SQLite, make sure you install
    the `sqlite` [optional dependency](https://docs.mau.fi/bridges/python/optional-dependencies.html).
  * **Alembic is no longer used**, schema migrations happen automatically on startup.
  * **The automatic database migration requires you to be on the latest legacy
    database version.** If you were running any v0.10.x version, you should be on
    the latest version already. Otherwise, update to v0.10.2 first, upgrade the
    database with `alembic`, then upgrade to v0.11.0 (or higher).
* Added support for contact messages.
* Added support for Telegram sponsored messages in channels.
  * Only applies to broadcast channels with 1000+ members
    (as per <https://t.me/durov/172>).
  * Only applies if you're using puppeting with a normal user account,
    because bots can't get sponsored messages.
* Fixed non-supergroup member sync incorrectly kicking one user from the Matrix
  side if there was no limit on the number of members to sync (broke in v0.10.2).
* Updated animated sticker conversion to support [lottieconverter r0.2]
  (thanks to [@sot-tech] in [#694]).
* Updated Docker image to Alpine 3.15.
* Formatted all code using [black](https://github.com/psf/black)
  and [isort](https://github.com/PyCQA/isort).

[lottieconverter r0.2]: https://github.com/sot-tech/LottieConverter/releases/tag/r0.2
[#694]: https://github.com/mautrix/telegram/pull/694

# v0.10.2 (2021-11-13)

### Added
* Added extensions when bridging unnamed files from Telegram.
* Added support for custom bridge bot welcome messages
  (thanks to [@justinbot] in [#676]).

### Improved
* Improved handling authorization errors if the bridge was logged out remotely.
* Updated room syncer to use existing power levels to find appropriate levels
  for admins and normal users instead of hardcoding 50 and 0.
* Updated to Telegram API layer 133 to handle 64-bit user/chat/channel IDs.
* Stopped logging message contents when message handling failed
  (thanks to [@justinbot] in [#681]).
* Removed Element iOS compatibility hack from non-sticker files.
* Made `max_initial_member_sync` work for non-supergroups too
  (thanks to [@tadzik] in [#680]).
* SQLite is now supported for the crypto database. Pickle is no longer supported.
  If you were using pickle, the bridge will create a new e2ee session and store
  the data in SQLite this time.

### Fixed
* Fixed generating reply fallbacks to encrypted messages.
* Fixed chat sync failing if the member list contained banned users.

[@justinbot]: https://github.com/justinbot
[@tadzik]: https://github.com/tadzik
[#676]: https://github.com/mautrix/telegram/pull/676
[#680]: https://github.com/mautrix/telegram/pull/680
[#681]: https://github.com/mautrix/telegram/pull/681

# v0.10.1 (2021-08-19)

**N.B.** Docker images have moved from `dock.mau.dev/tulir/mautrix-telegram`
to `dock.mau.dev/mautrix/telegram`. New versions are only available at the new
path.

### Added

* Warning when bridging existing room if bridge bot doesn't have redaction
  permissions.
* Custom flag to invite events that will be auto-accepted using double puppeting.
* Custom flags for animated stickers (same as what gifs already had).

### Improved
* Updated to Telethon 1.22.
* Updated Docker image to Alpine 3.14.

### Fixed
* Fixed Bridging Matrix location messages with additional flags in `geo_uri`.
* Editing encrypted messages will no longer add an asterisk on Telegram.
* Matrix typing notifications won't be echoed back for double puppeted users anymore.
* `AuthKeyDuplicatedError` is now handled properly instead of making the user
  get stuck.
* Fixed `public_portals` setting not being respected on room creation.

# v0.10.0 (2021-06-14)

* Added options to bridge archive, pin and mute status from Telegram to Matrix.
* Added custom fields in Matrix events indicating Telegram gifs.
* Allowed zero-width joiners in displaynames so things like multi-part emoji
  would work correctly.
* Fixed Telegram->Matrix typing notifications.

## rc1 (2021-04-05)

### Added
* Support for multiple pins from/to Telegram.
* Option to resolve redirects when joining invite links, for people who use
  custom URLs as invite links.
* Command to update about section in Telegram profile info
  (thanks to [@MadhuranS] in [#599]).
* Own read marker/unread status from Telegram is now synced to Matrix after backfilling.
* Support for showing the individual slots in 🎰 dice rolls from Telegram.

### Improved
* Improved invite link regex to allow joining with less precise invite links.
* Invite links can be customized with the `--uses=<amount>` and
  `--expire=<delta>` flags for `!tg invite-link`.
* Read receipts where the target message is unknown will now cause the chat to
  be marked as fully read instead of the read receipt event being ignored.
* WebP stickers are now sent as-is without converting to png.
* Default power levels in rooms now allow enabling encryption with PL 50 if
  e2be is enabled in config (thanks to [@Rafaeltheraven] in [#550]).
* Updated Docker image to Alpine 3.13 and removed all edge repo stuff.

### Fixed
* Matrix->Telegram location message bridging no longer flips the coordinates.
* Fixed some user displaynames constantly changing between contact/non-contact
  names and other similar cases.

[@Rafaeltheraven]: https://github.com/Rafaeltheraven
[@MadhuranS]: https://github.com/MadhuranS
[#550]: https://github.com/mautrix/telegram/pull/550
[#599]: https://github.com/mautrix/telegram/pull/599

# v0.9.0 (2020-11-17)

* Fixed cleaning unidentified rooms.

## rc3 (2020-11-12)

### Added
* Added retrying message sending if server returns 502.

### Fixed
* Fixed Matrix → Telegram name mentions.
* Fixed some bugs with replies.

## rc2 (2020-11-06)

### Improved
* Ephemeral event handling should be faster by not checking the database for
  user existence.
* Using the register command now sends a link to the Telegram terms of service.
* The `bridge_connected` metric is now only set for users who are logged in.

### Fixed
* Fixed bug where syncing members sometimes kicked ghosts of users who were
  actually still in the chat.
* Fixed sending captions to Telegram with `!tg caption` (broken in rc1).
* Logging out will now delete private chat portals, instead of only kicking the
  user and leaving the portal in a broken state.
* Unbridging direct chat portals is now possible.

## rc1 (2020-10-24)

### Breaking changes
* Prometheus metric names are now prefixed with `bridge_`.
* An entrypoint script is no longer automatically generated. This won't affect
  most users, as `python -m mautrix_telegram` has been the official way to start
  the bridge for a long time.

### Added
* Support for logging in by scanning a QR code from another Telegram client.
* Automatic backfilling of old messages when creating portals.
* Automatic backfilling of missed messages when starting bridge.
* Option to update `m.direct` list when using double puppeting.
* PNG thumbnails for animated stickers when converted to webm.
* Support for receiving ephemeral events pushed directly with [MSC2409]
  (requires Synapse 1.22 or higher).

### Improved
* Switched end-to-bridge encryption to mautrix-python instead of a hacky
  matrix-nio solution.
* End-to-bridge encryption no longer requires `login_shared_secret`, it uses
  [MSC2778] instead (requires Synapse 1.21 or higher).
* The bridge info state event is now updated whenever the chat name or avatar changes.
* Double puppeting is no longer limited to users on the same homeserver as the bridge.
* Delivery receipts are no longer sent in unencrypted private chat portals, as
  the bridge bot is usually not present in them.

### Fixed
* File captions are now sent as a separate message like photo captions.
* The relaybot no longer drops Telegram messages with commands.
* Bridging events of a user whose power level is malformed (i.e. a string
  instead of an integer) now works.

[MSC2409]: https://github.com/matrix-org/matrix-spec-proposals/pull/2409
[MSC2778]: https://github.com/matrix-org/matrix-spec-proposals/pull/2778

# v0.8.2 (2020-07-27)

* Fixed deleting messages from Matrix.
* Fixed Alpine edge dependencies in Docker image.

Note: this release is not on PyPI, as the only changes were a mautrix-python
update (v0.5.8) and a fix to the Docker image.

# v0.8.1 (2020-06-08)

* Fixed starting bridge for the first time failing due to not registering the bridge bot.
* Updated Docker image to Alpine 3.12.

# v0.8.0 (2020-06-03)

* Updated to mautrix-python 0.5.0 and matrix-nio 0.12.0.

## rc5 (2020-05-30)

* Added option to disable removing avatars from Telegram ghosts.
* Added option to send delivery error notices.
* Added option to send delivery receipts.
* Bumped maximum Telethon version to 1.14.
* Possibly fixed infinite loop of avatar changes when using double puppeting.

## rc3 (2020-05-22)

* Moved private information to trace log level.
* Added `private_chat_portal_meta` option. This is implicitly enabled when
  encryption is enabled, it was only added as an option for instances with
  encryption disabled.
* Removed avatars are now synced properly from Telegram, instead of leaving the
  last known avatar forever.
* Fixed admin detection on Telegram-side relaybot commands
  (thanks to [@davidmehren] in [#468]).
* Fixed bug handling `ChatForbidden` when syncing chats.

[@davidmehren]: https://github.com/davidmehren
[#468]: https://github.com/mautrix/telegram/pull/468

## rc2 (2020-05-20)

* Implemented [MSC2346]: Bridge information state event for newly created rooms.
* Fixed `sync_direct_chats` option creating non-working portals.
* Fixed video thumbnailing sometimes leaving behind downloaded videos in `/tmp`.

[MSC2346]: https://github.com/matrix-org/matrix-spec-proposals/pull/2346

## rc1 (2020-04-25)

### Added
* Command for backfilling room history from Telegram.
* arm64 support in docker images.
* Optional end-to-bridge encryption support.
  See [docs](https://docs.mau.fi/bridges/general/end-to-bridge-encryption.html) for more info.
* Bridging for Telegram dice roll messages.

### Fixed
* Riot iOS not showing stickers properly.
* Updated to Telethon 1.13 to fix bugs like [#443].

[#443]: https://github.com/mautrix/telegram/issues/443

# v0.7.2 (2020-04-04)

* No changes since rc1.

## rc1 (2020-02-08)

* Fixed enabling double puppeting causing saved messages to become unusable.
* Fixed receiving channel messages when `ignore_own_incoming_events` was enabled.

# v0.7.1 (2020-02-04)

* Fixed missing responses in logout provisioning API.

## rc2 (2020-01-25)

* Fixed import in database migration script (thanks to [@cubesky] in [#409]).
* Fixed relaybot messages being allowed through to Matrix even when
  `ignore_own_incoming_events` was set to `true`.

[@cubesky]: https://github.com/cubesky
[#409]: https://github.com/mautrix/telegram/pull/409

## rc1 (2020-01-11)

* Fixed incorrect parameter name causing `!tg config set` to throw an error.
* Fixed potential dictionary size changed during iteration crash.

# v0.7.0 (2019-12-28)

* No changes since rc4.

## rc4 (2019-12-25)

* Fixed handling of Matrix `m.emote` events.

## rc3 (2019-12-25)

* Added option to log in to custom puppet with shared secret
  (<https://github.com/devture/matrix-synapse-shared-secret-auth>).
* Updated Docker image to Alpine 3.11.
* Improved displayname syncing by trusting any displayname if user is not a contact.
* Fixed error when cleaning up rooms.
* Fixed stack traces being printed to non-admin users.
* Fixed invite rejections being handles as leaves.
* Fixed `version` command output in CI docker builds not showing the correct
  git commit hash.

## rc2 (2019-12-01)

* Added command to get bridge version.
* Made bridge refuse to start if config contains example values.
* Removed some debug stack traces.
* Ignored `ChatForbidden` when syncing chats that was causing the sync to fail.
* Fixed DB migration causing some incorrect values to be left behind.

## rc1 (2019-11-30)

### Important changes
* Dropped Python 3.5 compatibility.
* Moved docker registry to [dock.mau.dev](https://mau.dev/tulir/mautrix-telegram/container_registry).

### Added
* Support for bridging animated stickers (thanks to [@sot-tech] in [#366]).
  * Requires [LottieConverter](https://github.com/sot-tech/LottieConverter),
    which is included in the docker image.
  * Can be [configured](https://github.com/mautrix/telegram/blob/v0.7.0-rc1/example-config.yaml#L174-L187).
* Support for MTProxy (thanks to [@sot-tech] in [#344]).
* [Config option](https://github.com/mautrix/telegram/blob/v0.7.0-rc1/example-config.yaml#L117-L118)
  for max length of displayname, with the default being 100.
* Separate [config option](https://github.com/mautrix/telegram/blob/v0.7.0-rc1/example-config.yaml#L232-L238)
  for `m.emote` formatting of logged in users.
* Streamed file transfers and parallel telegram file download/upload.
  * Files are streamed from telegram servers to the media repo rather than
    downloading the whole file into memory.
  * File transfers use multiple connections to telegram servers to transfer faster.
  * Parallel and streamed file transfers can be enabled in the
    [config](https://github.com/mautrix/telegram/blob/v0.7.0-rc1/example-config.yaml#L166-L169).
* Command to set caption for files and images when sending to telegram.
* Bridging bans to telegram.
* Helm chart.

### Improved
* Switched from mautrix-appservice-python to [mautrix-python](https://github.com/mautrix/python).
* Users with Matrix puppeting can now bridge their "Saved Messages" chat.
* The bridge will refuse to start without access to the example config file.
* Changed default port to 29317.
* Mentions are now marked as read on Telegram when bridging read receipts using
  double puppeting.
* Kicking or banning the bridge bot will now unbridge the room.
* Shrinked Docker image from 151mb to 77mb.

### Fixed
* The bridge will no longer crash if one user's startup fails.
* (hopefully) Incorrect peer type being saved into database in some cases.
* File names when bridging to Telegram.
* Alembic config interpolating passwords with `%`.
* A single chat failing to sync preventing any chat from syncing.
* Users logged in as a bot not receiving any messages.
* Username matching being case-sensitive in the database and preventing
  telegram->matrix pillifying.
* IndexError if running `!tg set-pl` with no parameters.

[@sot-tech]: https://github.com/sot-tech
[#344]: https://github.com/mautrix/telegram/pull/344
[#366]: https://github.com/mautrix/telegram/pull/366

# v0.6.0 (2019-07-09)

* Fixed vulnerability in event handling.

## rc2 (2019-07-06)

* Nested formatting is now supported by Telegram, so the bridge also supports it.
* Strikethrough and underline are now bridged into native Telegram formatting
  rather than unicode hacks.
* Fixed displayname not updating for users who the bridge only saw via a logged
  in user who had the problematic user in their contacts.
* Fixed handling unsupported media.
* Added handling for `FileIdInvalidError` in file transfers that could disrupt
  `sync`s.

## rc1 (2019-06-22)

### Added
* Native Matrix edit support and new fallback format.
* Config options for `retry_delay` and other TelegramClient constructor fields.
* Config option for maximum document size to let through the bridge.
* External URL field for chat and private channel messages.
* Telegram user info (puppet displayname & avatar) is now updated every time
  the user sends a message.
* Command to change Telegram displayname.
* Possibility to override config fields with environment variables
  (thanks to [@pacien] in [#332]).

### Improved
* Simplified bridged poll message.
* Telegram user info updates are now accepted from any logged in user as long
  as the logged in user doesn't see a phone number for the Telegram user.
* Some image errors are now handled by resending the image as a document.
* Made getting started more user-friendly.
* Updated to Telethon 1.8.

### Fixed
* Portal peer type not being saved in database after Telegram chat upgrade.
* Newlines in unformatted messages not being bridged when using relaybot.
* Mime type info field for stickers converted to PNG.
* Content after newlines being stripped in messages sent by some clients.
* Potential `NoneType is not iterable` exception when logging out
  (thanks to [@turt2live] in [#315]).
* Handling of Matrix messages where `m.relates_to` is null.
* Internal server error when logging in with an account on another DC.
* Spaces between command and arguments are now trimmed.
* Changed migrations to use `batch_alter_table` for adding columns to have less
  warnings with SQLite.
* Error when `ping`ing without being logged in.
* Terminating sessions with negative hashes.
* State cache not being updated when sending events, causing invalid cache if
  the server doesn't echo the sent events.

[@pacien]: https://github.com/pacien
[#315]: https://github.com/mautrix/telegram/pull/315
[#332]: https://github.com/mautrix/telegram/pull/332

# v0.5.2 (2019-05-25)

* Fixed null `m.relates_to`'s that break Synapse 0.99.5.

# v0.5.1 (2019-03-21)

* Fixed Python 3.5 compatibility.
* Fixed DBMS migration script.

# v0.5.0 (2019-03-19)

* Replaced rawgit with cdnjs in public website as rawgit is deprecated.
* Fixed login command throwing error when web login is enabled.
* Updated telethon-session-sqlalchemy to fix logging into an account on another DC.
* Stopped adding reply fallback to caption when sending caption and image as
  separate messages.

## rc4 (2019-03-16)

* Added verbose flag to migration script.
* Added pytest setup and some tests (thanks to [@V02460] in [#290]).
* Fixed scripts (DBMS migration and Telematrix import) not being included in builds.
* Fixed some database problems.
* Removed remaining traces of ORM that might have been the causes of some other
  database problems.
* Removed option to use lxml in HTML parsing as it was messing up emoji offset
  handling. The new HTML parser supports using the default python HTMLParser
  class since 0.5.0rc1, so lxml wasn't really useful anway.

[#290]: https://github.com/mautrix/telegram/pull/290

## rc3 (2019-02-16)

* Fixed bridging documents without thumbnails to Matrix.
* Added option to set maximum size of image to send to Telegram. Images above
  the size limit will be sent as documents without the compression Telegram
  applies to images.
* Fixed saving user portals and contacts.
* Added Telegram -> Matrix poll bridging and a command to vote in polls.

## rc2 (2019-02-15)

* Added missing future-fstrings comments that caused the bridge to not start on
  Python 3.5.
* Fixed handling of document thumbnails.
* Fixed private chat portals failing to be created.
* Made relaybot handle Telegram chat upgrade events.

## rc1 (2019-02-14)

### Added
* More config options
  * Option to to use Telegram test servers.
  * Option to disable link previews on Telegram.
  * Option to disable startup sync.
  * Option to skip deleted members when syncing member lists.
  * Option to change number of dialogs to handle in startup sync.
* More commands
  * `username` for setting Telegram username.
  * `sync-state` for updating Matrix room state cache.
  * `matrix-ping` for checking Matrix login status (thanks to [@krombel] in [#271]).
  * `clear-db-cache` for clearing internal database caches.
  * `reload-user` for reloading and reconnecting a Telegram user.
  * `session` for listing and terminating other Telegram sessions.
  * Added argument to `login` to allow admins to log in for other users.
* Added warning when logging in that it grants the bridge full access to the
  telegram account.
* Telegram->Matrix bridging:
  * Telegram games
  * Message pins in normal groups
  * Custom message for unsupported media like polls
* Added client ID in logs when making requests to telegram.
* Added handling for Matrix room upgrades.

### Improved
* Removed lxml dependency from the new HTML parser and removed the old parser
  completely.
* Switched mautrix-appservice-python state store and most mautrix-telegram
  tables to SQLAlchemy core. This should speed things up and reduce problems
  with the ORM getting stuck.
* `ensure_started` is now only called for logged in users, which should improve
  performance for large instances.
* Displayname template extras (e.g. the `(Telegram)` suffix) are now stripped
  when mentioning Telegram users with no username.
* Updated Telethon.
* Switched Dockerfile to use setup.py for dependencies to avoid dependency
  updates breaking stuff.
* The telematrix import script will now warn about and skip over duplicate portals.
* Relaybot will now be used for users who have logged in, but are not in the chat.

### Fixed
* Bug where stickers with an unidentified emoji failed to bridge.
* Invalid letter prefixes in clean-rooms output.
* Messages forwarded from channels showing up as "Unknown source".
* Matrix->Telegram room avatar bridging.

[@krombel]: https://github.com/krombel
[#271]: https://github.com/mautrix/telegram/pull/271

# v0.4.0 (2018-11-28)

* No changes since rc2.

## rc2 (2018-11-15)

* Fixed kicking Telegram puppets from Matrix.

## rc1 (2018-11-15)

### Added
* Flag to indicate if user can unbridge portal in provisioning API
  (thanks to [@turt2live] in [#225]).
* Option to send captions as second message (replaces option to send caption
  in `body`.
* Room-specific settings.

### Improved
* (internal) Added type hints everywhere (mostly thanks to [@V02460] in [#206]).
* Telegram->Matrix formatter now uses `<pre>` tags for multiline code even if
  said code was in the telegram equivalent of inline code tags.
* Better bullets and linebreak handling in Matrix->Telegram formatter.
* Logging in will now show your phone number instead of `@None` if you don't
  have a username.
* Significantly improved performance on high-load instances (t2bot.io) by
  moving most used database tables to SQLAlchemy Core.

### Fixed
* Bugs that caused database migrations to fail in some cases.
* Editing the config (e.g. whitelisting chats) corrupting the config.
* Negative numbers (chat IDs) in `/connect` of the provisioning API
  (thanks to [@turt2live] in [#223]).
* Relaybot creating portals automatically when receiving message.
* Not being able to use a bridge bot localpart that would also match the puppet
  localpart format.
* Matrix login sync failing completely if the homeserver stopped during a sync
  response.
* Errors when cleaning rooms.
* Bridging code blocks without a language.
* Error and lost messages when trying to bridge PM from new users in some cases.
* Logging in with an account that someone has already logged in failing
  silently and then breaking the bridge.
* Relaybot message when adding/removing Matrix displaynames.

[@V02460]: https://github.com/V02460
[#206]: https://github.com/mautrix/telegram/pull/206
[#223]: https://github.com/mautrix/telegram/pull/223
[#225]: https://github.com/mautrix/telegram/pull/225

# v0.3.0 (2018-08-15)

* Added database URI format examples.
* Bumped maximum Telethon version to 1.2, possibly fixing the catch_up option.

## rc3 (2018-08-08)

* Improved Telegram message deduplication options.
  * Added pre-send message database check for deduplication.
  * Made dedup cache queue length configurable.

## rc2 (2018-08-06)

* Added option to change max body size for AS API.
* Fixed a minor error regarding power level changes (thanks to [@turt2live] in [#203]).
* Updated minimum mautrix-appservice version to include some recent bugfixes.

[@turt2live]: https://github.com/turt2live
[#203]: https://github.com/mautrix/telegram/pull/203

## rc1 (2018-08-05)

### Added
* Logging in with a bot
  (see [docs](https://docs.mau.fi/bridges/python/telegram/authentication.html#bot-token) for usage).
  * You can log in with a personal Telegram bot to appear almost like a real
    user without logging in with a real Telegram account.
* Replacing your Telegram account's Matrix puppet with your Matrix account
  (see [docs](https://docs.mau.fi/bridges/general/double-puppeting.html) for usage).
* Formatting options for relaybot messages.
  * Real displaynames are now supported and enabled by default.
  * State events (join/leave/name change) can be independently disabled by
    setting the format to a blank string.
* New config sections
  * Proper log config, including logging to file (by default)
  * Proxy support (requires installing PySocks)
  * Separate field for appservice address for homeserver
    (useful if using a reverse proxy).
* New permission levels to allow initiating bridges without allowing puppeting
  and to allow Telegram puppeting without allowing Matrix puppeting.
* Telematrix import script (see [docs](https://docs.mau.fi/bridges/python/telegram/migrating-from-telematrix.html) for usage).
* Provisioning API (see [docs](https://docs.mau.fi/bridges/python/telegram/provisioning-api.html) for more info).
* DBMS migration script (see [docs](https://docs.mau.fi/bridges/python/telegram/dbms-migration.html) for usage).

### Improved
* Tabs are now replaced with 4 spaces so that Telegram servers wouldn't change
  the message.
* Help page now detects your permissions and only shows commands you can use.
* Moved Matrix state cache to the main database. This means that the
  `mx-state.json` file is no longer needed and all non-config data is
  stored in the main database.
* Better lxml-based HTML parser for Matrix->Telegram formatting bridging.
  lxml is still optional, so the old parser is used as fallback if lxml is not
  installed.
* Disabled Telegram->Matrix bridging of messages sent by the relaybot.
  Can be re-enabled in config if necessary.

### Fixed
* A `ValueError` in some cases when syncing power levels.
* Telegram connections being created for unauthenticated users possibly
  triggering spam protection connection delays in the Telegram servers.
* Logging out if a portal had been deleted/unbridged.

# v0.2.0 (2018-06-08)

* No changes since rc6.

## rc6 (2018-06-06)

* Added warning about `delete-portal` kicking all room members.
* Fixed error when upgrading/creating SQLite database.

## rc5 (2018-06-01)

* Fixed relaybot automatically creating portal rooms when invited to Telegram chat ([#145]).
* Fixed kicking Telegram puppets and fix error message when bridging chats you've left.
* Fixed integrity error deleting portals from database.

[#145]: https://github.com/mautrix/telegram/issues/145

## rc4 (2018-05-29)

* ~~Fixed~~ Added Postgres compatibility.
* Fixed manual bridging (`!tg bridge`) for unauthenticated users.
* Fixed inviting unauthenticated Matrix users from Telegram (via `/invite <mxid>`).
* Changed Alembic to read database path from the config, so editing `alembic.ini`
  is no longer necessary. Use `alembic -x config=/path/to/config.yaml ...` to
  specify the config path.

## rc3 (2018-05-25)

* Reworked Dockerfile to remove virtualenv and use Alpine packages (thanks to
  [@jcgruenhage] in [#142]). This fixes webp->png conversion for stickers.

[#142]: https://github.com/mautrix/telegram/pull/142

## rc2 (2018-05-21)

* Added Dockerfile (thanks to [@jcgruenhage] in [#136]).

[#136]: https://github.com/mautrix/telegram/pull/136
[@jcgruenhage]: https://github.com/jcgruenhage

## rc1 (2018-05-19)

* Added
  * Option to exclude telegram chats from being bridged.
  * Support for using a relay bot to relay messages for unauthenticated users
  * Bridging for message pinning and room mentions/pills.
  * Matrix->Telegram sticker bridging.
  * `!command` to `/command` conversion at the start of Matrix message text.
  * Conversion of t.me message links to matrix.to message links
  * Timestamp massaging (bridge Telegram timestamps to Matrix)
  * Support for out-of-Matrix login (useful if you don't want your 2FA password to be stored in the homeserver)
  * Optional HQ gif/video thumbnails using moviepy.
  * Option to send bot messages as `m.notice`
* Improved deduplication
  * Matrix file uploads are now reused if the same Telegram file (e.g. a sticker) is sent multiple times
  * Room metadata changes and other non-message actions are now deduplicated
* Improved formatting bridging
* Improved Telegram user display name handling in cases where one or more users have set custom display names for other users.
* Fixed Alembic setup and removed automatic database generation.
* Fixed outgoing message deduplication in cases where message is sent to other clients before responding to the sender.
* Moved mautrix-appservice-python to separate repository.
* Switched to telethon-session-sqlalchemy to have the session databases in the main database.
* Switched license from GPLv3 to AGPLv3
* Probably a bunch of other stuff I forgot

# v0.1.1 (2018-02-18)

Fixed bridging formatted messages from Matrix to Telegram

# v0.1.0 (2018-02-17)

First release.

Things work.
