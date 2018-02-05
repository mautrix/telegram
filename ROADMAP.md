# Features & roadmap

* Matrix → Telegram
  * [x] Plaintext messages
  * [x] Formatted messages
    * [ ] Bot commands (!command -> /command)
    * [x] Mentions
  * [x] Rich quotes
  * [ ] Locations (not implemented in Riot)
  * [x] Images
  * [x] Files
  * [x] Message redactions
  * [ ] † Presence
  * [ ] † Typing notifications
  * [ ] † Read receipts
  * [ ] Pinning messages
  * [x] Power level
    * [x] Normal chats
      * [ ] Non-hardcoded PL requirements
	* [x] Supergroups/channels
	  * [ ] Precise bridging (non-hardcoded PL requirements, bridge specific permissions, etc..)
  * [ ] Membership actions
    * [x] Inviting
      * [x] Puppets
      * [x] Matrix users who have logged into Telegram
    * [x] Kicking
    * [ ] Joining
      * [ ] Chat name as alias
      * [ ] ‡ Chat invite link as alias
    * [x] Leaving
  * [x] Room metadata changes (name, topic, avatar)
  * [x] Initial room metadata
  * [ ] User metadata
    * [ ] Initial displayname/username/avatar at register
    * [ ] ‡ Changes to displayname/avatar
* Telegram → Matrix
  * [x] Plaintext messages
  * [x] Formatted messages
    * [x] Bot commands (/command -> !command)
    * [x] Mentions
  * [x] Replies
  * [x] Forwards
  * [x] Images
  * [x] Locations
  * [x] Stickers
  * [x] Audio messages
  * [x] Video messages
  * [x] Documents
  * [ ] Message deletions (no way to tell difference between user-specific deletion and global deletion)
  * [ ] Message edits (not supported in Matrix)
  * [x] Avatars
  * [x] Presence
  * [x] Typing notifications
  * [x] Read receipts (private chat only)
  * [x] Pinning messages
  * [x] Admin/chat creator status
  * [ ] Supergroup/channel permissions (precise per-user not supported in Matrix)
  * [x] Membership actions
    * [x] Inviting
    * [x] Kicking
    * [x] Joining/leaving
  * [ ] Chat metadata changes
    * [x] Title
    * [x] Avatar
    * [ ] † About text
    * [ ] † Public channel username
  * [x] Initial chat metadata (about text missing)
  * [x] User metadata
    * [x] Initial displayname/avatar
    * [x] Changes to displayname/avatar
  * [x] Supergroup upgrade
* Misc
  * [x] Automatic portal creation
    * [x] At startup
    * [x] When receiving invite or message
  * [x] Private chat creation by inviting Matrix puppet of Telegram user to new room
  * [ ] Option to use bot to relay messages for unauthenticated Matrix users
  * [ ] Option to use own Matrix account for messages sent from other Telegram clients
* [Commands](https://github.com/tulir/mautrix-telegram/wiki/Management-commands)
  * [x] Logging in and out (`login` + code entering, `logout`)
  * [ ] Registering (`register`)
  * [x] Searching for users (`search`)
    * [ ] Searching contacts locally
  * [x] Starting private chats (`pm`)
  * [x] Joining chats with invite links (`join`)
  * [x] Creating a Telegram chat for an existing Matrix room (`create`)
  * [x] Upgrading the chat of a portal room into a supergroup (`upgrade`)
  * [x] Change username of supergroup/channel (`groupname`)
  * [x] Getting the Telegram invite link to a Matrix room (`invitelink`)
  * [x] Clean up and forget a portal room (`deleteportal`)

† Information not automatically sent from source, i.e. implementation may not be possible  
‡ Maybe, i.e. this feature may or may not be implemented at some point
