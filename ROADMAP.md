# Features & roadmap

* Matrix → Telegram
  * [x] Message content (text, formatting, files, etc..)
  * [x] Message redactions
  * [x] Message edits
  * [ ] ‡ Message history
  * [x] Presence
  * [x] Typing notifications*
  * [x] Read receipts*
  * [x] Pinning messages*
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
    * [x] Polls
	* [x] Games
	* [ ] Buttons
  * [x] Message deletions
  * [x] Message edits
  * [ ] Message history
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
  * [x] Private chat creation by inviting Matrix puppet of Telegram user to new room
  * [x] Option to use bot to relay messages for unauthenticated Matrix users
  * [x] Option to use own Matrix account for messages sent from other Telegram clients
  * [ ] ‡ Calls (hard, not yet supported by Telethon)
  * [ ] ‡ Secret chats (not yet supported by Telethon)
  * [ ] ‡ E2EE in Matrix rooms (not yet supported 

\* Requires [double puppeting](https://github.com/tulir/mautrix-telegram/wiki/Authentication#replacing-telegram-accounts-matrix-puppet-with-matrix-account) to be enabled  
† Information not automatically sent from source, i.e. implementation may not be possible  
‡ Maybe, i.e. this feature may or may not be implemented at some point
