# Features & roadmap

* Matrix → Telegram
  * [x] Message content (text, formatting, files, etc..)
  * [x] Message redactions
  * [x] Message reactions
  * [x] Message edits
  * [ ] ‡ Message history
  * [ ] Presence
  * [x] Typing notifications
  * [x] Read receipts
  * [ ] Pinning messages
  * [ ] Power level
  * [ ] Membership actions (invite/kick/join/leave)
  * [ ] Room metadata changes (name, topic, avatar)
  * [ ] Initial room metadata
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
  * [x] Message history
    * [x] Automatically when creating portal
    * [x] Automatically for missed messages
  * [x] Avatars
  * [ ] Presence
  * [x] Typing notifications
  * [x] Read receipts (DMs only)
  * [ ] Pinning messages
  * [x] Admin/chat creator status
  * [x] Supergroup/channel permissions (precise per-user permissions not supported in Matrix)
  * [x] Membership actions (invite/kick/join/leave)
  * [ ] Chat metadata changes
    * [x] Title
    * [x] Avatar
    * [ ] † About text
    * [ ] † Public channel username
  * [x] Initial chat metadata (about text missing)
  * [x] User metadata (displayname/avatar)
  * [x] Supergroup upgrade
  * [x] Topics (spaces)
* Misc
  * [x] Automatic portal creation
    * [x] At startup
    * [x] When receiving invite or message
  * [x] Private chat creation by inviting Matrix ghost of Telegram user to new room
  * [x] Option to use bot to relay messages for unauthenticated Matrix users (relaybot)
  * [x] Option to use own Matrix account for messages sent from other Telegram clients (double puppeting)
  * [ ] ‡ Calls
  * [ ] ‡ Secret chats (i.e. end-to-bridge encryption on Telegram)

† Information not automatically sent from source, i.e. implementation may not be possible  
‡ Maybe, i.e. this feature may or may not be implemented at some point
