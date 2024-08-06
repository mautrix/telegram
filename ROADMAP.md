# Features & roadmap

* Matrix → Telegram
  * [x] Message content (text, formatting, files, etc..)
  * [x] Message redactions
  * [x] Message reactions
  * [x] Message edits
  * [ ] ‡ Message history
  * [ ] Presence
  * [ ] Typing notifications
  * [ ] Read receipts
  * [ ] Pinning messages
  * [ ] Power level
    * [ ] Normal chats
      * [ ] Non-hardcoded PL requirements
    * [ ] Supergroups/channels
      * [ ] Precise bridging (non-hardcoded PL requirements, bridge specific permissions, etc..)
  * [ ] Membership actions (invite/kick/join/leave)
  * [ ] Room metadata changes (name, topic, avatar)
  * [ ] Initial room metadata
  * [ ] User metadata
    * [ ] Initial displayname/username/avatar at register
    * [ ] ‡ Changes to displayname/avatar
* Telegram → Matrix
  * [x] Message content (text, formatting, files, etc..)
  * [ ] Advanced message content/media
    * [x] Custom emojis
    * [ ] Polls
    * [ ] Games
    * [ ] Buttons
  * [x] Message deletions
  * [x] Message reactions
  * [x] Message edits
  * [ ] Message history
    * [ ] Manually (`!tg backfill`)
    * [ ] Automatically when creating portal
    * [ ] Automatically for missed messages
  * [ ] Avatars
  * [ ] Presence
  * [ ] Typing notifications
  * [ ] Read receipts (private chat only)
  * [ ] Pinning messages
  * [ ] Admin/chat creator status
  * [ ] Supergroup/channel permissions (precise per-user permissions not supported in Matrix)
  * [ ] Membership actions (invite/kick/join/leave)
  * [ ] Chat metadata changes
    * [x] Title
    * [x] Avatar
    * [ ] † About text
    * [ ] † Public channel username
  * [x] Initial chat metadata (about text missing)
  * [ ] User metadata (displayname/avatar)
  * [ ] Supergroup upgrade
* Misc
  * [ ] Automatic portal creation
    * [ ] At startup
    * [ ] When receiving invite or message
  * [ ] Portal creation by inviting Matrix puppet of Telegram user to new room
  * [ ] Option to use bot to relay messages for unauthenticated Matrix users (relaybot)
  * [ ] Option to use own Matrix account for messages sent from other Telegram clients (double puppeting)
  * [ ] ‡ Calls
  * [ ] ‡ Secret chats (i.e. end-to-bridge encryption on Telegram)
  * [ ] End-to-bridge encryption in Matrix rooms (see [docs](https://docs.mau.fi/bridges/general/end-to-bridge-encryption.html))

† Information not automatically sent from source, i.e. implementation may not be possible  
‡ Maybe, i.e. this feature may or may not be implemented at some point
