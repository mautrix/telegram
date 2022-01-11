## Element fork

The Element fork includes the following changes:
 - Add config limits for portal rooms https://github.com/mautrix/telegram/pull/469
 - Allow disabling user status updates from Telegram side https://github.com/vector-im/mautrix-telegram/pull/9
 - Add `psycopg2`, `uvloop` to requirements.txt, install_requires https://github.com/vector-im/mautrix-telegram/pull/10/files
 - Don't block connections on startup https://github.com/vector-im/mautrix-telegram/pull/20
 - Add metrics for Appservice's Connection Pool stats https://github.com/vector-im/mautrix-telegram/pull/22, https://github.com/vector-im/mautrix-telegram/pull/27, https://github.com/vector-im/mautrix-telegram/pull/29
 - Don't require bot startup for bridge startup https://github.com/vector-im/mautrix-telegram/pull/24
 - Add `telegram.liveness_timeout` config to change `/_matrix/mau/live` when Telegram connections are no longer being received https://github.com/vector-im/mautrix-telegram/pull/23

Some changes that appear here may get upstreamed to https://github.com/mautrix/telegram, and will be removed from
the list when they appear in both versions.

Tagged versions will appear as `v{UPSTREAM-VERSION}-mod-{VERSION}`

E.g. The third modification release to 1.0 of the upstream bridge would be `v1.0-mod-3`.

# mautrix-telegram
![Languages](https://img.shields.io/github/languages/top/mautrix/telegram.svg)
[![License](https://img.shields.io/github/license/mautrix/telegram.svg)](LICENSE)
[![Release](https://img.shields.io/github/release/mautrix/telegram/all.svg)](https://github.com/mautrix/telegram/releases)
[![GitLab CI](https://mau.dev/mautrix/telegram/badges/master/pipeline.svg)](https://mau.dev/mautrix/telegram/container_registry)
[![Code style](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Imports](https://img.shields.io/badge/%20imports-isort-%231674b1?style=flat&labelColor=ef8336)](https://pycqa.github.io/isort/)

A Matrix-Telegram hybrid puppeting/relaybot bridge.
## Sponsors
* [Joel Lehtonen / Zouppen](https://github.com/zouppen)

### Documentation
All setup and usage instructions are located on
[docs.mau.fi](https://docs.mau.fi/bridges/python/telegram/index.html).
Some quick links:

* [Bridge setup](https://docs.mau.fi/bridges/python/setup/index.html?bridge=telegram)
  (or [with Docker](https://docs.mau.fi/bridges/python/setup/docker.html?bridge=telegram))
* Basic usage: [Authentication](https://docs.mau.fi/bridges/python/telegram/authentication.html),
  [Creating chats](https://docs.mau.fi/bridges/python/telegram/creating-and-managing-chats.html),
  [Relaybot setup](https://docs.mau.fi/bridges/python/telegram/relay-bot.html)

### Features & Roadmap
[ROADMAP.md](https://github.com/mautrix/telegram/blob/master/ROADMAP.md)
contains a general overview of what is supported by the bridge.

## Discussion
Matrix room: [`#telegram:maunium.net`](https://matrix.to/#/#telegram:maunium.net)

Telegram chat: [`mautrix_telegram`](https://t.me/mautrix_telegram) (bridged to Matrix room)

## Preview
![Preview](preview.png)
