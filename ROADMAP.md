# Features & roadmap

* Matrix → Telegram
  * [x] Message content (text, formatting, files, etc..)
  * [x] Message redactions
  * [x] Message reactions
  * [x] Message edits
  * [ ] ‡ Message history
  * [x] Presence
  * [x] Typing notifications
  * [x] Read receipts
  * [x] Pinning messages
  * [x] Power level
    * [x] Normal chats
      * [ ] Non-hardcoded PL requirements
    * [x] Supergroups/channels
      * [ ] Precise bridging (non-hardcoded PL requirements, bridge specific permissions, etc..)
  * [x] Membership actions (invite/kick/join/leave)
  * [x] Room metadata changes (name, topic, avatar)
  * [x] Initial room metadata
  * [ ] User metadata
    * [ ] Initial displayname/username/avatar at register
    * [ ] ‡ Changes to displayname/avatar
* Telegram → Matrix
  * [x] Message content (text, formatting, files, etc..)
  * [ ] Advanced message content/media
    * [x] Custom emojis
    * [x] Polls
	* [x] Games
	* [ ] Buttons
  * [x] Message deletions
  * [x] Message reactions
  * [x] Message edits
  * [x] Message history
    * [x] Manually (`!tg backfill`)
    * [x] Automatically when creating portal
    * [x] Automatically for missed messages
  * [x] Avatars
  * [x] Presence
  * [x] Typing notifications
  * [x] Read receipts (private chat only)
  * [x] Pinning messages
  * [x] Admin/chat creator status
  * [ ] Supergroup/channel permissions (precise per-user permissions not supported in Matrix)
  * [x] Membership actions (invite/kick/join/leave)
  * [ ] Chat metadata changes
    * [x] Title
    * [x] Avatar
    * [ ] † About text
    * [ ] † Public channel username
  * [x] Initial chat metadata (about text missing)
  * [x] User metadata (displayname/avatar)
  * [x] Supergroup upgrade
* Misc
  * [x] Automatic portal creation
    * [x] At startup
    * [x] When receiving invite or message
  * [x] Portal creation by inviting Matrix puppet of Telegram user to new room
  * [x] Option to use bot to relay messages for unauthenticated Matrix users (relaybot)
  * [x] Option to use own Matrix account for messages sent from other Telegram clients (double puppeting)
  * [ ] ‡ Calls (hard, not yet supported by Telethon)
  * [ ] ‡ Secret chats (i.e. End-to-bridge encryption on Telegram)
  * [x] End-to-bridge encryption in Matrix rooms (see [docs](https://docs.mau.fi/bridges/general/end-to-bridge-encryption.html))

† Information not automatically sent from source, i.e. implementation may not be possible  
‡ Maybe, i.e. this feature may or may not be implemented at some point
