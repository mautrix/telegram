package humanise

import "github.com/gotd/td/tgerr"

func Error(err error) string {
	switch {
	case tgerr.Is(err, "2FA_CONFIRM_WAIT_X"):
		return "The account is 2FA protected so it will be deleted in a week. Otherwise it can be reset in {seconds}"
	case tgerr.Is(err, "ABOUT_TOO_LONG"):
		return "The provided bio is too long"
	case tgerr.Is(err, "ACCESS_TOKEN_EXPIRED"):
		return "Bot token expired"
	case tgerr.Is(err, "ACCESS_TOKEN_INVALID"):
		return "The provided token is not valid"
	case tgerr.Is(err, "ACTIVE_USER_REQUIRED"):
		return "The method is only available to already activated users"
	case tgerr.Is(err, "ADMINS_TOO_MUCH"):
		return "Too many admins"
	case tgerr.Is(err, "ADMIN_ID_INVALID"):
		return "The specified admin ID is invalid"
	case tgerr.Is(err, "ADMIN_RANK_EMOJI_NOT_ALLOWED"):
		return "Emoji are not allowed in admin titles or ranks"
	case tgerr.Is(err, "ADMIN_RANK_INVALID"):
		return "The given admin title or rank was invalid (possibly larger than 16 characters)"
	case tgerr.Is(err, "ALBUM_PHOTOS_TOO_MANY"):
		return "Too many photos were included in the album"
	case tgerr.Is(err, "API_ID_INVALID"):
		return "The api_id/api_hash combination is invalid"
	case tgerr.Is(err, "API_ID_PUBLISHED_FLOOD"):
		return "This API id was published somewhere, you can't use it now"
	case tgerr.Is(err, "ARTICLE_TITLE_EMPTY"):
		return "The title of the article is empty"
	case tgerr.Is(err, "AUDIO_CONTENT_URL_EMPTY"):
		return "The remote URL specified in the content field is empty"
	case tgerr.Is(err, "AUDIO_TITLE_EMPTY"):
		return "The title attribute of the audio must be non-empty"
	case tgerr.Is(err, "AUTH_BYTES_INVALID"):
		return "The provided authorization is invalid"
	case tgerr.Is(err, "AUTH_KEY_DUPLICATED"):
		return "The authorization key (session file) was used under two different IP addresses simultaneously, and can no longer be used. Use the same session exclusively, or use different sessions"
	case tgerr.Is(err, "AUTH_KEY_INVALID"):
		return "The key is invalid"
	case tgerr.Is(err, "AUTH_KEY_PERM_EMPTY"):
		return "The method is unavailable for temporary authorization key, not bound to permanent"
	case tgerr.Is(err, "AUTH_KEY_UNREGISTERED"):
		return "The key is not registered in the system"
	case tgerr.Is(err, "AUTH_RESTART"):
		return "Restart the authorization process"
	case tgerr.Is(err, "AUTH_TOKEN_ALREADY_ACCEPTED"):
		return "The authorization token was already used"
	case tgerr.Is(err, "AUTH_TOKEN_EXCEPTION"):
		return "An error occurred while importing the auth token"
	case tgerr.Is(err, "AUTH_TOKEN_EXPIRED"):
		return "The provided authorization token has expired and the updated QR-code must be re-scanned"
	case tgerr.Is(err, "AUTH_TOKEN_INVALID"):
		return "An invalid authorization token was provided"
	case tgerr.Is(err, "AUTH_TOKEN_INVALID2"):
		return "An invalid authorization token was provided"
	case tgerr.Is(err, "AUTH_TOKEN_INVALIDX"):
		return "The specified auth token is invalid"
	case tgerr.Is(err, "AUTOARCHIVE_NOT_AVAILABLE"):
		return "You cannot use this feature yet"
	case tgerr.Is(err, "BANK_CARD_NUMBER_INVALID"):
		return "Incorrect credit card number"
	case tgerr.Is(err, "BANNED_RIGHTS_INVALID"):
		return "You cannot use that set of permissions in this request, i.e. restricting view_messages as a default"
	case tgerr.Is(err, "BASE_PORT_LOC_INVALID"):
		return "Base port location invalid"
	case tgerr.Is(err, "BOTS_TOO_MUCH"):
		return "There are too many bots in this chat/channel"
	case tgerr.Is(err, "BOT_CHANNELS_NA"):
		return "Bots can't edit admin privileges"
	case tgerr.Is(err, "BOT_COMMAND_DESCRIPTION_INVALID"):
		return "The command description was empty, too long or had invalid characters used"
	case tgerr.Is(err, "BOT_COMMAND_INVALID"):
		return "The specified command is invalid"
	case tgerr.Is(err, "BOT_DOMAIN_INVALID"):
		return "The domain used for the auth button does not match the one configured in @BotFather"
	case tgerr.Is(err, "BOT_GAMES_DISABLED"):
		return "Bot games cannot be used in this type of chat"
	case tgerr.Is(err, "BOT_GROUPS_BLOCKED"):
		return "This bot can't be added to groups"
	case tgerr.Is(err, "BOT_INLINE_DISABLED"):
		return "This bot can't be used in inline mode"
	case tgerr.Is(err, "BOT_INVALID"):
		return "This is not a valid bot"
	case tgerr.Is(err, "BOT_METHOD_INVALID"):
		return "The API access for bot users is restricted. The method you tried to invoke cannot be executed as a bot"
	case tgerr.Is(err, "BOT_MISSING"):
		return "This method can only be run by a bot"
	case tgerr.Is(err, "BOT_ONESIDE_NOT_AVAIL"):
		return "Bots can't pin messages in PM just for themselves"
	case tgerr.Is(err, "BOT_PAYMENTS_DISABLED"):
		return "This method can only be run by a bot"
	case tgerr.Is(err, "BOT_POLLS_DISABLED"):
		return "You cannot create polls under a bot account"
	case tgerr.Is(err, "BOT_RESPONSE_TIMEOUT"):
		return "The bot did not answer to the callback query in time"
	case tgerr.Is(err, "BOT_SCORE_NOT_MODIFIED"):
		return "The score wasn't modified"
	case tgerr.Is(err, "BROADCAST_CALLS_DISABLED"):
		return ""
	case tgerr.Is(err, "BROADCAST_FORBIDDEN"):
		return "The request cannot be used in broadcast channels"
	case tgerr.Is(err, "BROADCAST_ID_INVALID"):
		return "The channel is invalid"
	case tgerr.Is(err, "BROADCAST_PUBLIC_VOTERS_FORBIDDEN"):
		return "You cannot broadcast polls where the voters are public"
	case tgerr.Is(err, "BROADCAST_REQUIRED"):
		return "The request can only be used with a broadcast channel"
	case tgerr.Is(err, "BUTTON_DATA_INVALID"):
		return "The provided button data is invalid"
	case tgerr.Is(err, "BUTTON_TEXT_INVALID"):
		return "The specified button text is invalid"
	case tgerr.Is(err, "BUTTON_TYPE_INVALID"):
		return "The type of one of the buttons you provided is invalid"
	case tgerr.Is(err, "BUTTON_URL_INVALID"):
		return "Button URL invalid"
	case tgerr.Is(err, "BUTTON_USER_PRIVACY_RESTRICTED"):
		return "The privacy setting of the user specified in a [inputKeyboardButtonUserProfile](/constructor/inputKeyboardButtonUserProfile) button do not allow creating such a button"
	case tgerr.Is(err, "CALL_ALREADY_ACCEPTED"):
		return "The call was already accepted"
	case tgerr.Is(err, "CALL_ALREADY_DECLINED"):
		return "The call was already declined"
	case tgerr.Is(err, "CALL_OCCUPY_FAILED"):
		return "The call failed because the user is already making another call"
	case tgerr.Is(err, "CALL_PEER_INVALID"):
		return "The provided call peer object is invalid"
	case tgerr.Is(err, "CALL_PROTOCOL_FLAGS_INVALID"):
		return "Call protocol flags invalid"
	case tgerr.Is(err, "CDN_METHOD_INVALID"):
		return "This method cannot be invoked on a CDN server. Refer to https://core.telegram.org/cdn#schema for available methods"
	case tgerr.Is(err, "CDN_UPLOAD_TIMEOUT"):
		return "A server-side timeout occurred while reuploading the file to the CDN DC"
	case tgerr.Is(err, "CHANNELS_ADMIN_LOCATED_TOO_MUCH"):
		return "The user has reached the limit of public geogroups"
	case tgerr.Is(err, "CHANNELS_ADMIN_PUBLIC_TOO_MUCH"):
		return "You're admin of too many public channels, make some channels private to change the username of this channel"
	case tgerr.Is(err, "CHANNELS_TOO_MUCH"):
		return "You have joined too many channels/supergroups"
	case tgerr.Is(err, "CHANNEL_BANNED"):
		return "The channel is banned"
	case tgerr.Is(err, "CHANNEL_FORUM_MISSING"):
		return ""
	case tgerr.Is(err, "CHANNEL_ID_INVALID"):
		return "The specified supergroup ID is invalid"
	case tgerr.Is(err, "CHANNEL_INVALID"):
		return "Invalid channel object. Make sure to pass the right types, for instance making sure that the request is designed for channels or otherwise look for a different one more suited"
	case tgerr.Is(err, "CHANNEL_PARICIPANT_MISSING"):
		return "The current user is not in the channel"
	case tgerr.Is(err, "CHANNEL_PRIVATE"):
		return "The channel specified is private and you lack permission to access it. Another reason may be that you were banned from it"
	case tgerr.Is(err, "CHANNEL_PUBLIC_GROUP_NA"):
		return "channel/supergroup not available"
	case tgerr.Is(err, "CHANNEL_TOO_BIG"):
		return ""
	case tgerr.Is(err, "CHANNEL_TOO_LARGE"):
		return "Channel is too large to be deleted; this error is issued when trying to delete channels with more than 1000 members (subject to change)"
	case tgerr.Is(err, "CHAT_ABOUT_NOT_MODIFIED"):
		return "About text has not changed"
	case tgerr.Is(err, "CHAT_ABOUT_TOO_LONG"):
		return "Chat about too long"
	case tgerr.Is(err, "CHAT_ADMIN_INVITE_REQUIRED"):
		return "You do not have the rights to do this"
	case tgerr.Is(err, "CHAT_ADMIN_REQUIRED"):
		return "Chat admin privileges are required to do that in the specified chat (for example, to send a message in a channel which is not yours), or invalid permissions used for the channel or group"
	case tgerr.Is(err, "CHAT_DISCUSSION_UNALLOWED"):
		return ""
	case tgerr.Is(err, "CHAT_FORBIDDEN"):
		return "You cannot write in this chat"
	case tgerr.Is(err, "CHAT_FORWARDS_RESTRICTED"):
		return "You can't forward messages from a protected chat"
	case tgerr.Is(err, "CHAT_GET_FAILED"):
		return ""
	case tgerr.Is(err, "CHAT_GUEST_SEND_FORBIDDEN"):
		return "You join the discussion group before commenting, see [here](/api/discussion#requiring-users-to-join-the-group) for more info"
	case tgerr.Is(err, "CHAT_ID_EMPTY"):
		return "The provided chat ID is empty"
	case tgerr.Is(err, "CHAT_ID_GENERATE_FAILED"):
		return "Failure while generating the chat ID"
	case tgerr.Is(err, "CHAT_ID_INVALID"):
		return "Invalid object ID for a chat. Make sure to pass the right types, for instance making sure that the request is designed for chats (not channels/megagroups) or otherwise look for a different one more suited\\nAn example working with a megagroup and AddChatUserRequest, it will fail because megagroups are channels. Use InviteToChannelRequest instead"
	case tgerr.Is(err, "CHAT_INVALID"):
		return "The chat is invalid for this request"
	case tgerr.Is(err, "CHAT_INVITE_PERMANENT"):
		return "You can't set an expiration date on permanent invite links"
	case tgerr.Is(err, "CHAT_LINK_EXISTS"):
		return "The chat is linked to a channel and cannot be used in that request"
	case tgerr.Is(err, "CHAT_NOT_MODIFIED"):
		return "The chat or channel wasn't modified (title, invites, username, admins, etc. are the same)"
	case tgerr.Is(err, "CHAT_RESTRICTED"):
		return "The chat is restricted and cannot be used in that request"
	case tgerr.Is(err, "CHAT_REVOKE_DATE_UNSUPPORTED"):
		return "`min_date` and `max_date` are not available for using with non-user peers"
	case tgerr.Is(err, "CHAT_SEND_GAME_FORBIDDEN"):
		return "You can't send a game to this chat"
	case tgerr.Is(err, "CHAT_SEND_GIFS_FORBIDDEN"):
		return "You can't send gifs in this chat"
	case tgerr.Is(err, "CHAT_SEND_INLINE_FORBIDDEN"):
		return "You cannot send inline results in this chat"
	case tgerr.Is(err, "CHAT_SEND_MEDIA_FORBIDDEN"):
		return "You can't send media in this chat"
	case tgerr.Is(err, "CHAT_SEND_POLL_FORBIDDEN"):
		return "You can't send polls in this chat"
	case tgerr.Is(err, "CHAT_SEND_STICKERS_FORBIDDEN"):
		return "You can't send stickers in this chat"
	case tgerr.Is(err, "CHAT_TITLE_EMPTY"):
		return "No chat title provided"
	case tgerr.Is(err, "CHAT_TOO_BIG"):
		return "This method is not available for groups with more than `chat_read_mark_size_threshold` members, [see client configuration](https://core.telegram.org/api/config#client-configuration)"
	case tgerr.Is(err, "CHAT_WRITE_FORBIDDEN"):
		return "You can't write in this chat"
	case tgerr.Is(err, "CHP_CALL_FAIL"):
		return "The statistics cannot be retrieved at this time"
	case tgerr.Is(err, "CODE_EMPTY"):
		return "The provided code is empty"
	case tgerr.Is(err, "CODE_HASH_INVALID"):
		return "Code hash invalid"
	case tgerr.Is(err, "CODE_INVALID"):
		return "Code invalid (i.e. from email)"
	case tgerr.Is(err, "CONNECTION_API_ID_INVALID"):
		return "The provided API id is invalid"
	case tgerr.Is(err, "CONNECTION_APP_VERSION_EMPTY"):
		return "App version is empty"
	case tgerr.Is(err, "CONNECTION_DEVICE_MODEL_EMPTY"):
		return "Device model empty"
	case tgerr.Is(err, "CONNECTION_LANG_PACK_INVALID"):
		return "The specified language pack is not valid. This is meant to be used by official applications only so far, leave it empty"
	case tgerr.Is(err, "CONNECTION_LAYER_INVALID"):
		return "The very first request must always be InvokeWithLayerRequest"
	case tgerr.Is(err, "CONNECTION_NOT_INITED"):
		return "Connection not initialized"
	case tgerr.Is(err, "CONNECTION_SYSTEM_EMPTY"):
		return "Connection system empty"
	case tgerr.Is(err, "CONNECTION_SYSTEM_LANG_CODE_EMPTY"):
		return "The system language string was empty during connection"
	case tgerr.Is(err, "CONTACT_ADD_MISSING"):
		return "Contact to add is missing"
	case tgerr.Is(err, "CONTACT_ID_INVALID"):
		return "The provided contact ID is invalid"
	case tgerr.Is(err, "CONTACT_NAME_EMPTY"):
		return "The provided contact name cannot be empty"
	case tgerr.Is(err, "CONTACT_REQ_MISSING"):
		return "Missing contact request"
	case tgerr.Is(err, "CREATE_CALL_FAILED"):
		return "An error occurred while creating the call"
	case tgerr.Is(err, "CURRENCY_TOTAL_AMOUNT_INVALID"):
		return "The total amount of all prices is invalid"
	case tgerr.Is(err, "DATA_INVALID"):
		return "Encrypted data invalid"
	case tgerr.Is(err, "DATA_JSON_INVALID"):
		return "The provided JSON data is invalid"
	case tgerr.Is(err, "DATA_TOO_LONG"):
		return "Data too long"
	case tgerr.Is(err, "DATE_EMPTY"):
		return "Date empty"
	case tgerr.Is(err, "DC_ID_INVALID"):
		return "This occurs when an authorization is tried to be exported for the same data center one is currently connected to"
	case tgerr.Is(err, "DH_G_A_INVALID"):
		return "g_a invalid"
	case tgerr.Is(err, "DOCUMENT_INVALID"):
		return "The document file was invalid and can't be used in inline mode"
	case tgerr.Is(err, "EDIT_BOT_INVITE_FORBIDDEN"):
		return "Normal users can't edit invites that were created by bots"
	case tgerr.Is(err, "EMAIL_HASH_EXPIRED"):
		return "The email hash expired and cannot be used to verify it"
	case tgerr.Is(err, "EMAIL_INVALID"):
		return "The given email is invalid"
	case tgerr.Is(err, "EMAIL_UNCONFIRMED"):
		return "Email unconfirmed"
	case tgerr.Is(err, "EMAIL_UNCONFIRMED_X"):
		return "Email unconfirmed, the length of the code must be {code_length}"
	case tgerr.Is(err, "EMAIL_VERIFY_EXPIRED"):
		return "The verification email has expired"
	case tgerr.Is(err, "EMOJI_INVALID"):
		return "The specified theme emoji is valid"
	case tgerr.Is(err, "EMOJI_NOT_MODIFIED"):
		return "The theme wasn't changed"
	case tgerr.Is(err, "EMOTICON_EMPTY"):
		return "The emoticon field cannot be empty"
	case tgerr.Is(err, "EMOTICON_INVALID"):
		return "The specified emoticon cannot be used or was not a emoticon"
	case tgerr.Is(err, "EMOTICON_STICKERPACK_MISSING"):
		return "The emoticon sticker pack you are trying to get is missing"
	case tgerr.Is(err, "ENCRYPTED_MESSAGE_INVALID"):
		return "Encrypted message invalid"
	case tgerr.Is(err, "ENCRYPTION_ALREADY_ACCEPTED"):
		return "Secret chat already accepted"
	case tgerr.Is(err, "ENCRYPTION_ALREADY_DECLINED"):
		return "The secret chat was already declined"
	case tgerr.Is(err, "ENCRYPTION_DECLINED"):
		return "The secret chat was declined"
	case tgerr.Is(err, "ENCRYPTION_ID_INVALID"):
		return "The provided secret chat ID is invalid"
	case tgerr.Is(err, "ENCRYPTION_OCCUPY_FAILED"):
		return "TDLib developer claimed it is not an error while accepting secret chats and 500 is used instead of 420"
	case tgerr.Is(err, "ENTITIES_TOO_LONG"):
		return "It is no longer possible to send such long data inside entity tags (for example inline text URLs)"
	case tgerr.Is(err, "ENTITY_BOUNDS_INVALID"):
		return "Some of provided entities have invalid bounds (length is zero or out of the boundaries of the string)"
	case tgerr.Is(err, "ENTITY_MENTION_USER_INVALID"):
		return "You can't use this entity"
	case tgerr.Is(err, "ERROR_TEXT_EMPTY"):
		return "The provided error message is empty"
	case tgerr.Is(err, "EXPIRE_DATE_INVALID"):
		return "The specified expiration date is invalid"
	case tgerr.Is(err, "EXPIRE_FORBIDDEN"):
		return ""
	case tgerr.Is(err, "EXPORT_CARD_INVALID"):
		return "Provided card is invalid"
	case tgerr.Is(err, "EXTERNAL_URL_INVALID"):
		return "External URL invalid"
	case tgerr.Is(err, "FIELD_NAME_EMPTY"):
		return "The field with the name FIELD_NAME is missing"
	case tgerr.Is(err, "FIELD_NAME_INVALID"):
		return "The field with the name FIELD_NAME is invalid"
	case tgerr.Is(err, "FILEREF_UPGRADE_NEEDED"):
		return "The file reference needs to be refreshed before being used again"
	case tgerr.Is(err, "FILE_CONTENT_TYPE_INVALID"):
		return "File content-type is invalid"
	case tgerr.Is(err, "FILE_EMTPY"):
		return "An empty file was provided"
	case tgerr.Is(err, "FILE_ID_INVALID"):
		return "The provided file id is invalid. Make sure all parameters are present, have the correct type and are not empty (ID, access hash, file reference, thumb size ...)"
	case tgerr.Is(err, "FILE_MIGRATE_X"):
		return "The file to be accessed is currently stored in DC {new_dc}"
	case tgerr.Is(err, "FILE_PARTS_INVALID"):
		return "The number of file parts is invalid"
	case tgerr.Is(err, "FILE_PART_0_MISSING"):
		return "File part 0 missing"
	case tgerr.Is(err, "FILE_PART_EMPTY"):
		return "The provided file part is empty"
	case tgerr.Is(err, "FILE_PART_INVALID"):
		return "The file part number is invalid"
	case tgerr.Is(err, "FILE_PART_LENGTH_INVALID"):
		return "The length of a file part is invalid"
	case tgerr.Is(err, "FILE_PART_SIZE_CHANGED"):
		return "The file part size (chunk size) cannot change during upload"
	case tgerr.Is(err, "FILE_PART_SIZE_INVALID"):
		return "The provided file part size is invalid"
	case tgerr.Is(err, "FILE_PART_TOO_BIG"):
		return "The uploaded file part is too big"
	case tgerr.Is(err, "FILE_PART_X_MISSING"):
		return "Part {which} of the file is missing from storage"
	case tgerr.Is(err, "FILE_REFERENCE_EMPTY"):
		return "The file reference must exist to access the media and it cannot be empty"
	case tgerr.Is(err, "FILE_REFERENCE_EXPIRED"):
		return "The file reference has expired and is no longer valid or it belongs to self-destructing media and cannot be resent"
	case tgerr.Is(err, "FILE_REFERENCE_INVALID"):
		return "The file reference is invalid or you can't do that operation on such message"
	case tgerr.Is(err, "FILE_TITLE_EMPTY"):
		return "An empty file title was specified"
	case tgerr.Is(err, "FILTER_ID_INVALID"):
		return "The specified filter ID is invalid"
	case tgerr.Is(err, "FILTER_INCLUDE_EMPTY"):
		return "The include_peers vector of the filter is empty"
	case tgerr.Is(err, "FILTER_NOT_SUPPORTED"):
		return "The specified filter cannot be used in this context"
	case tgerr.Is(err, "FILTER_TITLE_EMPTY"):
		return "The title field of the filter is empty"
	case tgerr.Is(err, "FIRSTNAME_INVALID"):
		return "The first name is invalid"
	case tgerr.Is(err, "FLOOD_TEST_PHONE_WAIT_X"):
		return "A wait of {seconds} seconds is required in the test servers"
	case tgerr.Is(err, "FLOOD_WAIT_X"):
		return "A wait of {seconds} seconds is required"
	case tgerr.Is(err, "FOLDER_ID_EMPTY"):
		return "The folder you tried to delete was already empty"
	case tgerr.Is(err, "FOLDER_ID_INVALID"):
		return "The folder you tried to use was not valid"
	case tgerr.Is(err, "FRESH_CHANGE_ADMINS_FORBIDDEN"):
		return "Recently logged-in users cannot add or change admins"
	case tgerr.Is(err, "FRESH_CHANGE_PHONE_FORBIDDEN"):
		return "Recently logged-in users cannot use this request"
	case tgerr.Is(err, "FRESH_RESET_AUTHORISATION_FORBIDDEN"):
		return "The current session is too new and cannot be used to reset other authorisations yet"
	case tgerr.Is(err, "FROM_MESSAGE_BOT_DISABLED"):
		return "Bots can't use fromMessage min constructors"
	case tgerr.Is(err, "FROM_PEER_INVALID"):
		return "The given from_user peer cannot be used for the parameter"
	case tgerr.Is(err, "GAME_BOT_INVALID"):
		return "You cannot send that game with the current bot"
	case tgerr.Is(err, "GEO_POINT_INVALID"):
		return "Invalid geoposition provided"
	case tgerr.Is(err, "GIF_CONTENT_TYPE_INVALID"):
		return "GIF content-type invalid"
	case tgerr.Is(err, "GIF_ID_INVALID"):
		return "The provided GIF ID is invalid"
	case tgerr.Is(err, "GRAPH_EXPIRED_RELOAD"):
		return "This graph has expired, please obtain a new graph token"
	case tgerr.Is(err, "GRAPH_INVALID_RELOAD"):
		return "Invalid graph token provided, please reload the stats and provide the updated token"
	case tgerr.Is(err, "GRAPH_OUTDATED_RELOAD"):
		return "Data can't be used for the channel statistics, graphs outdated"
	case tgerr.Is(err, "GROUPCALL_ADD_PARTICIPANTS_FAILED"):
		return ""
	case tgerr.Is(err, "GROUPCALL_ALREADY_DISCARDED"):
		return "The group call was already discarded"
	case tgerr.Is(err, "GROUPCALL_ALREADY_STARTED"):
		return "The groupcall has already started, you can join directly using [phone.joinGroupCall](https://core.telegram.org/method/phone.joinGroupCall)"
	case tgerr.Is(err, "GROUPCALL_FORBIDDEN"):
		return "The group call has already ended"
	case tgerr.Is(err, "GROUPCALL_INVALID"):
		return "The specified group call is invalid"
	case tgerr.Is(err, "GROUPCALL_JOIN_MISSING"):
		return "You haven't joined this group call"
	case tgerr.Is(err, "GROUPCALL_NOT_MODIFIED"):
		return "Group call settings weren't modified"
	case tgerr.Is(err, "GROUPCALL_SSRC_DUPLICATE_MUCH"):
		return "The app needs to retry joining the group call with a new SSRC value"
	case tgerr.Is(err, "GROUPED_MEDIA_INVALID"):
		return "Invalid grouped media"
	case tgerr.Is(err, "GROUP_CALL_INVALID"):
		return "Group call invalid"
	case tgerr.Is(err, "HASH_INVALID"):
		return "The provided hash is invalid"
	case tgerr.Is(err, "HIDE_REQUESTER_MISSING"):
		return "The join request was missing or was already handled"
	case tgerr.Is(err, "HISTORY_GET_FAILED"):
		return "Fetching of history failed"
	case tgerr.Is(err, "IMAGE_PROCESS_FAILED"):
		return "Failure while processing image"
	case tgerr.Is(err, "IMPORT_FILE_INVALID"):
		return "The file is too large to be imported"
	case tgerr.Is(err, "IMPORT_FORMAT_UNRECOGNIZED"):
		return "Unknown import format"
	case tgerr.Is(err, "IMPORT_ID_INVALID"):
		return "The specified import ID is invalid"
	case tgerr.Is(err, "INLINE_BOT_REQUIRED"):
		return "The action must be performed through an inline bot callback"
	case tgerr.Is(err, "INLINE_RESULT_EXPIRED"):
		return "The inline query expired"
	case tgerr.Is(err, "INPUT_CONSTRUCTOR_INVALID"):
		return "The provided constructor is invalid"
	case tgerr.Is(err, "INPUT_FETCH_ERROR"):
		return "An error occurred while deserializing TL parameters"
	case tgerr.Is(err, "INPUT_FETCH_FAIL"):
		return "Failed deserializing TL payload"
	case tgerr.Is(err, "INPUT_FILTER_INVALID"):
		return "The search query filter is invalid"
	case tgerr.Is(err, "INPUT_LAYER_INVALID"):
		return "The provided layer is invalid"
	case tgerr.Is(err, "INPUT_METHOD_INVALID"):
		return "The invoked method does not exist anymore or has never existed"
	case tgerr.Is(err, "INPUT_REQUEST_TOO_LONG"):
		return "The input request was too long. This may be a bug in the library as it can occur when serializing more bytes than it should (like appending the vector constructor code at the end of a message)"
	case tgerr.Is(err, "INPUT_TEXT_EMPTY"):
		return "The specified text is empty"
	case tgerr.Is(err, "INPUT_USER_DEACTIVATED"):
		return "The specified user was deleted"
	case tgerr.Is(err, "INTERDC_X_CALL_ERROR"):
		return "An error occurred while communicating with DC {dc}"
	case tgerr.Is(err, "INTERDC_X_CALL_RICH_ERROR"):
		return "A rich error occurred while communicating with DC {dc}"
	case tgerr.Is(err, "INVITE_FORBIDDEN_WITH_JOINAS"):
		return "If the user has anonymously joined a group call as a channel, they can't invite other users to the group call because that would cause deanonymization, because the invite would be sent using the original user ID, not the anonymized channel ID"
	case tgerr.Is(err, "INVITE_HASH_EMPTY"):
		return "The invite hash is empty"
	case tgerr.Is(err, "INVITE_HASH_EXPIRED"):
		return "The chat the user tried to join has expired and is not valid anymore"
	case tgerr.Is(err, "INVITE_HASH_INVALID"):
		return "The invite hash is invalid"
	case tgerr.Is(err, "INVITE_REQUEST_SENT"):
		return "You have successfully requested to join this chat or channel"
	case tgerr.Is(err, "INVITE_REVOKED_MISSING"):
		return "The specified invite link was already revoked or is invalid"
	case tgerr.Is(err, "INVOICE_PAYLOAD_INVALID"):
		return "The specified invoice payload is invalid"
	case tgerr.Is(err, "JOIN_AS_PEER_INVALID"):
		return "The specified peer cannot be used to join a group call"
	case tgerr.Is(err, "LANG_CODE_INVALID"):
		return "The specified language code is invalid"
	case tgerr.Is(err, "LANG_CODE_NOT_SUPPORTED"):
		return "The specified language code is not supported"
	case tgerr.Is(err, "LANG_PACK_INVALID"):
		return "The provided language pack is invalid"
	case tgerr.Is(err, "LASTNAME_INVALID"):
		return "The last name is invalid"
	case tgerr.Is(err, "LIMIT_INVALID"):
		return "An invalid limit was provided. See https://core.telegram.org/api/files#downloading-files"
	case tgerr.Is(err, "LINK_NOT_MODIFIED"):
		return "The channel is already linked to this group"
	case tgerr.Is(err, "LOCATION_INVALID"):
		return "The location given for a file was invalid. See https://core.telegram.org/api/files#downloading-files"
	case tgerr.Is(err, "MAX_DATE_INVALID"):
		return "The specified maximum date is invalid"
	case tgerr.Is(err, "MAX_ID_INVALID"):
		return "The provided max ID is invalid"
	case tgerr.Is(err, "MAX_QTS_INVALID"):
		return "The provided QTS were invalid"
	case tgerr.Is(err, "MD5_CHECKSUM_INVALID"):
		return "The MD5 check-sums do not match"
	case tgerr.Is(err, "MEDIA_CAPTION_TOO_LONG"):
		return "The caption is too long"
	case tgerr.Is(err, "MEDIA_EMPTY"):
		return "The provided media object is invalid or the current account may not be able to send it (such as games as users)"
	case tgerr.Is(err, "MEDIA_GROUPED_INVALID"):
		return "You tried to send media of different types in an album"
	case tgerr.Is(err, "MEDIA_INVALID"):
		return "Media invalid"
	case tgerr.Is(err, "MEDIA_NEW_INVALID"):
		return "The new media to edit the message with is invalid (such as stickers or voice notes)"
	case tgerr.Is(err, "MEDIA_PREV_INVALID"):
		return "The old media cannot be edited with anything else (such as stickers or voice notes)"
	case tgerr.Is(err, "MEDIA_TTL_INVALID"):
		return ""
	case tgerr.Is(err, "MEGAGROUP_ID_INVALID"):
		return "The group is invalid"
	case tgerr.Is(err, "MEGAGROUP_PREHISTORY_HIDDEN"):
		return "You can't set this discussion group because it's history is hidden"
	case tgerr.Is(err, "MEGAGROUP_REQUIRED"):
		return "The request can only be used with a megagroup channel"
	case tgerr.Is(err, "MEMBER_NO_LOCATION"):
		return "An internal failure occurred while fetching user info (couldn't find location)"
	case tgerr.Is(err, "MEMBER_OCCUPY_PRIMARY_LOC_FAILED"):
		return "Occupation of primary member location failed"
	case tgerr.Is(err, "MESSAGE_AUTHOR_REQUIRED"):
		return "Message author required"
	case tgerr.Is(err, "MESSAGE_DELETE_FORBIDDEN"):
		return "You can't delete one of the messages you tried to delete, most likely because it is a service message."
	case tgerr.Is(err, "MESSAGE_EDIT_TIME_EXPIRED"):
		return "You can't edit this message anymore, too much time has passed since its creation."
	case tgerr.Is(err, "MESSAGE_EMPTY"):
		return "Empty or invalid UTF-8 message was sent"
	case tgerr.Is(err, "MESSAGE_IDS_EMPTY"):
		return "No message ids were provided"
	case tgerr.Is(err, "MESSAGE_ID_INVALID"):
		return "The specified message ID is invalid or you can't do that operation on such message"
	case tgerr.Is(err, "MESSAGE_NOT_MODIFIED"):
		return "Content of the message was not modified"
	case tgerr.Is(err, "MESSAGE_POLL_CLOSED"):
		return "The poll was closed and can no longer be voted on"
	case tgerr.Is(err, "MESSAGE_TOO_LONG"):
		return "Message was too long. Current maximum length is 4096 UTF-8 characters"
	case tgerr.Is(err, "METHOD_INVALID"):
		return "The API method is invalid and cannot be used"
	case tgerr.Is(err, "MIN_DATE_INVALID"):
		return "The specified minimum date is invalid"
	case tgerr.Is(err, "MSGID_DECREASE_RETRY"):
		return "The request should be retried with a lower message ID"
	case tgerr.Is(err, "MSG_ID_INVALID"):
		return "The message ID used in the peer was invalid"
	case tgerr.Is(err, "MSG_TOO_OLD"):
		return "[`chat_read_mark_expire_period` seconds](https://core.telegram.org/api/config#chat-read-mark-expire-period) have passed since the message was sent, read receipts were deleted"
	case tgerr.Is(err, "MSG_WAIT_FAILED"):
		return "A waiting call returned an error"
	case tgerr.Is(err, "MT_SEND_QUEUE_TOO_LONG"):
		return ""
	case tgerr.Is(err, "MULTI_MEDIA_TOO_LONG"):
		return "Too many media files were included in the same album"
	case tgerr.Is(err, "NEED_CHAT_INVALID"):
		return "The provided chat is invalid"
	case tgerr.Is(err, "NEED_MEMBER_INVALID"):
		return "The provided member is invalid or does not exist (for example a thumb size)"
	case tgerr.Is(err, "NETWORK_MIGRATE_X"):
		return "The source IP address is associated with DC {new_dc}"
	case tgerr.Is(err, "NEW_SALT_INVALID"):
		return "The new salt is invalid"
	case tgerr.Is(err, "NEW_SETTINGS_EMPTY"):
		return "No password is set on the current account, and no new password was specified in `new_settings`"
	case tgerr.Is(err, "NEW_SETTINGS_INVALID"):
		return "The new settings are invalid"
	case tgerr.Is(err, "NEXT_OFFSET_INVALID"):
		return "The value for next_offset is invalid. Check that it has normal characters and is not too long"
	case tgerr.Is(err, "NOT_ALLOWED"):
		return ""
	case tgerr.Is(err, "OFFSET_INVALID"):
		return "The given offset was invalid, it must be divisible by 1KB. See https://core.telegram.org/api/files#downloading-files"
	case tgerr.Is(err, "OFFSET_PEER_ID_INVALID"):
		return "The provided offset peer is invalid"
	case tgerr.Is(err, "OPTIONS_TOO_MUCH"):
		return "You defined too many options for the poll"
	case tgerr.Is(err, "OPTION_INVALID"):
		return "The option specified is invalid and does not exist in the target poll"
	case tgerr.Is(err, "PACK_SHORT_NAME_INVALID"):
		return "Invalid sticker pack name. It must begin with a letter, can't contain consecutive underscores and must end in \"_by_<bot username>\"."
	case tgerr.Is(err, "PACK_SHORT_NAME_OCCUPIED"):
		return "A stickerpack with this name already exists"
	case tgerr.Is(err, "PACK_TITLE_INVALID"):
		return "The stickerpack title is invalid"
	case tgerr.Is(err, "PARTICIPANTS_TOO_FEW"):
		return "Not enough participants"
	case tgerr.Is(err, "PARTICIPANT_CALL_FAILED"):
		return "Failure while making call"
	case tgerr.Is(err, "PARTICIPANT_ID_INVALID"):
		return "The specified participant ID is invalid"
	case tgerr.Is(err, "PARTICIPANT_JOIN_MISSING"):
		return "Trying to enable a presentation, when the user hasn't joined the Video Chat with [phone.joinGroupCall](https://core.telegram.org/method/phone.joinGroupCall)"
	case tgerr.Is(err, "PARTICIPANT_VERSION_OUTDATED"):
		return "The other participant does not use an up to date telegram client with support for calls"
	case tgerr.Is(err, "PASSWORD_EMPTY"):
		return "The provided password is empty"
	case tgerr.Is(err, "PASSWORD_HASH_INVALID"):
		return "The password (and thus its hash value) you entered is invalid"
	case tgerr.Is(err, "PASSWORD_MISSING"):
		return "The account must have 2-factor authentication enabled (a password) before this method can be used"
	case tgerr.Is(err, "PASSWORD_RECOVERY_EXPIRED"):
		return "The recovery code has expired"
	case tgerr.Is(err, "PASSWORD_RECOVERY_NA"):
		return "No email was set, can't recover password via email"
	case tgerr.Is(err, "PASSWORD_REQUIRED"):
		return "The account must have 2-factor authentication enabled (a password) before this method can be used"
	case tgerr.Is(err, "PASSWORD_TOO_FRESH_X"):
		return "The password was added too recently and {seconds} seconds must pass before using the method"
	case tgerr.Is(err, "PAYMENT_PROVIDER_INVALID"):
		return "The payment provider was not recognised or its token was invalid"
	case tgerr.Is(err, "PEER_FLOOD"):
		return "Too many requests"
	case tgerr.Is(err, "PEER_HISTORY_EMPTY"):
		return ""
	case tgerr.Is(err, "PEER_ID_INVALID"):
		return "An invalid Peer was used. Make sure to pass the right peer type and that the value is valid (for instance, bots cannot start conversations)"
	case tgerr.Is(err, "PEER_ID_NOT_SUPPORTED"):
		return "The provided peer ID is not supported"
	case tgerr.Is(err, "PERSISTENT_TIMESTAMP_EMPTY"):
		return "Persistent timestamp empty"
	case tgerr.Is(err, "PERSISTENT_TIMESTAMP_INVALID"):
		return "Persistent timestamp invalid"
	case tgerr.Is(err, "PERSISTENT_TIMESTAMP_OUTDATED"):
		return "Persistent timestamp outdated"
	case tgerr.Is(err, "PHONE_CODE_EMPTY"):
		return "The phone code is missing"
	case tgerr.Is(err, "PHONE_CODE_EXPIRED"):
		return "The confirmation code has expired"
	case tgerr.Is(err, "PHONE_CODE_HASH_EMPTY"):
		return "The phone code hash is missing"
	case tgerr.Is(err, "PHONE_CODE_INVALID"):
		return "The phone code entered was invalid"
	case tgerr.Is(err, "PHONE_HASH_EXPIRED"):
		return "An invalid or expired `phone_code_hash` was provided"
	case tgerr.Is(err, "PHONE_MIGRATE_X"):
		return "The phone number a user is trying to use for authorization is associated with DC {new_dc}"
	case tgerr.Is(err, "PHONE_NOT_OCCUPIED"):
		return "No user is associated to the specified phone number"
	case tgerr.Is(err, "PHONE_NUMBER_APP_SIGNUP_FORBIDDEN"):
		return "You can't sign up using this app"
	case tgerr.Is(err, "PHONE_NUMBER_BANNED"):
		return "The used phone number has been banned from Telegram and cannot be used anymore. Maybe check https://www.telegram.org/faq_spam"
	case tgerr.Is(err, "PHONE_NUMBER_FLOOD"):
		return "You asked for the code too many times."
	case tgerr.Is(err, "PHONE_NUMBER_INVALID"):
		return "The phone number is invalid"
	case tgerr.Is(err, "PHONE_NUMBER_OCCUPIED"):
		return "The phone number is already in use"
	case tgerr.Is(err, "PHONE_NUMBER_UNOCCUPIED"):
		return "The phone number is not yet being used"
	case tgerr.Is(err, "PHONE_PASSWORD_FLOOD"):
		return "You have tried logging in too many times"
	case tgerr.Is(err, "PHONE_PASSWORD_PROTECTED"):
		return "This phone is password protected"
	case tgerr.Is(err, "PHOTO_CONTENT_TYPE_INVALID"):
		return "Photo mime-type invalid"
	case tgerr.Is(err, "PHOTO_CONTENT_URL_EMPTY"):
		return "The content from the URL used as a photo appears to be empty or has caused another HTTP error"
	case tgerr.Is(err, "PHOTO_CROP_FILE_MISSING"):
		return "Photo crop file missing"
	case tgerr.Is(err, "PHOTO_CROP_SIZE_SMALL"):
		return "Photo is too small"
	case tgerr.Is(err, "PHOTO_EXT_INVALID"):
		return "The extension of the photo is invalid"
	case tgerr.Is(err, "PHOTO_FILE_MISSING"):
		return "Profile photo file missing"
	case tgerr.Is(err, "PHOTO_ID_INVALID"):
		return "Photo id is invalid"
	case tgerr.Is(err, "PHOTO_INVALID"):
		return "Photo invalid"
	case tgerr.Is(err, "PHOTO_INVALID_DIMENSIONS"):
		return "The photo dimensions are invalid (hint: `pip install pillow` for `send_file` to resize images)"
	case tgerr.Is(err, "PHOTO_SAVE_FILE_INVALID"):
		return "The photo you tried to send cannot be saved by Telegram. A reason may be that it exceeds 10MB. Try resizing it locally"
	case tgerr.Is(err, "PHOTO_THUMB_URL_EMPTY"):
		return "The URL used as a thumbnail appears to be empty or has caused another HTTP error"
	case tgerr.Is(err, "PINNED_DIALOGS_TOO_MUCH"):
		return "Too many pinned dialogs"
	case tgerr.Is(err, "PIN_RESTRICTED"):
		return "You can't pin messages in private chats with other people"
	case tgerr.Is(err, "POLL_ANSWERS_INVALID"):
		return "The poll did not have enough answers or had too many"
	case tgerr.Is(err, "POLL_ANSWER_INVALID"):
		return "One of the poll answers is not acceptable"
	case tgerr.Is(err, "POLL_OPTION_DUPLICATE"):
		return "A duplicate option was sent in the same poll"
	case tgerr.Is(err, "POLL_OPTION_INVALID"):
		return "A poll option used invalid data (the data may be too long)"
	case tgerr.Is(err, "POLL_QUESTION_INVALID"):
		return "The poll question was either empty or too long"
	case tgerr.Is(err, "POLL_UNSUPPORTED"):
		return "This layer does not support polls in the issued method"
	case tgerr.Is(err, "POLL_VOTE_REQUIRED"):
		return "Cast a vote in the poll before calling this method"
	case tgerr.Is(err, "POSTPONED_TIMEOUT"):
		return "The postponed call has timed out"
	case tgerr.Is(err, "PREMIUM_ACCOUNT_REQUIRED"):
		return "A premium account is required to execute this action"
	case tgerr.Is(err, "PREMIUM_CURRENTLY_UNAVAILABLE"):
		return ""
	case tgerr.Is(err, "PREVIOUS_CHAT_IMPORT_ACTIVE_WAIT_XMIN"):
		return "Similar to a flood wait, must wait {minutes} minutes"
	case tgerr.Is(err, "PRIVACY_KEY_INVALID"):
		return "The privacy key is invalid"
	case tgerr.Is(err, "PRIVACY_TOO_LONG"):
		return "Cannot add that many entities in a single request"
	case tgerr.Is(err, "PRIVACY_VALUE_INVALID"):
		return "The privacy value is invalid"
	case tgerr.Is(err, "PTS_CHANGE_EMPTY"):
		return "No PTS change"
	case tgerr.Is(err, "PUBLIC_CHANNEL_MISSING"):
		return "You can only export group call invite links for public chats or channels"
	case tgerr.Is(err, "PUBLIC_KEY_REQUIRED"):
		return "A public key is required"
	case tgerr.Is(err, "QUERY_ID_EMPTY"):
		return "The query ID is empty"
	case tgerr.Is(err, "QUERY_ID_INVALID"):
		return "The query ID is invalid"
	case tgerr.Is(err, "QUERY_TOO_SHORT"):
		return "The query string is too short"
	case tgerr.Is(err, "QUIZ_ANSWER_MISSING"):
		return "You can forward a quiz while hiding the original author only after choosing an option in the quiz"
	case tgerr.Is(err, "QUIZ_CORRECT_ANSWERS_EMPTY"):
		return "A quiz must specify one correct answer"
	case tgerr.Is(err, "QUIZ_CORRECT_ANSWERS_TOO_MUCH"):
		return "There can only be one correct answer"
	case tgerr.Is(err, "QUIZ_CORRECT_ANSWER_INVALID"):
		return "The correct answer is not an existing answer"
	case tgerr.Is(err, "QUIZ_MULTIPLE_INVALID"):
		return "A poll cannot be both multiple choice and quiz"
	case tgerr.Is(err, "RANDOM_ID_DUPLICATE"):
		return "You provided a random ID that was already used"
	case tgerr.Is(err, "RANDOM_ID_EMPTY"):
		return "Random ID empty"
	case tgerr.Is(err, "RANDOM_ID_INVALID"):
		return "A provided random ID is invalid"
	case tgerr.Is(err, "RANDOM_LENGTH_INVALID"):
		return "Random length invalid"
	case tgerr.Is(err, "RANGES_INVALID"):
		return "Invalid range provided"
	case tgerr.Is(err, "REACTIONS_TOO_MANY"):
		return "The message already has exactly `reactions_uniq_max` reaction emojis, you can't react with a new emoji, see [the docs for more info](/api/config#client-configuration)"
	case tgerr.Is(err, "REACTION_EMPTY"):
		return "No reaction provided"
	case tgerr.Is(err, "REACTION_INVALID"):
		return "Invalid reaction provided (only emoji are allowed)"
	case tgerr.Is(err, "REFLECTOR_NOT_AVAILABLE"):
		return "Invalid call reflector server"
	case tgerr.Is(err, "REG_ID_GENERATE_FAILED"):
		return "Failure while generating registration ID"
	case tgerr.Is(err, "REPLY_MARKUP_BUY_EMPTY"):
		return "Reply markup for buy button empty"
	case tgerr.Is(err, "REPLY_MARKUP_GAME_EMPTY"):
		return "The provided reply markup for the game is empty"
	case tgerr.Is(err, "REPLY_MARKUP_INVALID"):
		return "The provided reply markup is invalid"
	case tgerr.Is(err, "REPLY_MARKUP_TOO_LONG"):
		return "The data embedded in the reply markup buttons was too much"
	case tgerr.Is(err, "RESET_REQUEST_MISSING"):
		return "No password reset is in progress"
	case tgerr.Is(err, "RESULTS_TOO_MUCH"):
		return "You sent too many results, see https://core.telegram.org/bots/api#answerinlinequery for the current limit"
	case tgerr.Is(err, "RESULT_ID_DUPLICATE"):
		return "Duplicated IDs on the sent results. Make sure to use unique IDs"
	case tgerr.Is(err, "RESULT_ID_EMPTY"):
		return "Result ID empty"
	case tgerr.Is(err, "RESULT_ID_INVALID"):
		return "The given result cannot be used to send the selection to the bot"
	case tgerr.Is(err, "RESULT_TYPE_INVALID"):
		return "Result type invalid"
	case tgerr.Is(err, "REVOTE_NOT_ALLOWED"):
		return "You cannot change your vote"
	case tgerr.Is(err, "RIGHTS_NOT_MODIFIED"):
		return "The new admin rights are equal to the old rights, no change was made"
	case tgerr.Is(err, "RIGHT_FORBIDDEN"):
		return "Either your admin rights do not allow you to do this or you passed the wrong rights combination (some rights only apply to channels and vice versa)"
	case tgerr.Is(err, "RPC_CALL_FAIL"):
		return "Telegram is having internal issues, please try again later."
	case tgerr.Is(err, "RPC_MCGET_FAIL"):
		return "Telegram is having internal issues, please try again later."
	case tgerr.Is(err, "RSA_DECRYPT_FAILED"):
		return "Internal RSA decryption failed"
	case tgerr.Is(err, "SCHEDULE_BOT_NOT_ALLOWED"):
		return "Bots are not allowed to schedule messages"
	case tgerr.Is(err, "SCHEDULE_DATE_INVALID"):
		return "Invalid schedule date provided"
	case tgerr.Is(err, "SCHEDULE_DATE_TOO_LATE"):
		return "The date you tried to schedule is too far in the future (last known limit of 1 year and a few hours)"
	case tgerr.Is(err, "SCHEDULE_STATUS_PRIVATE"):
		return "You cannot schedule a message until the person comes online if their privacy does not show this information"
	case tgerr.Is(err, "SCHEDULE_TOO_MUCH"):
		return "You cannot schedule more messages in this chat (last known limit of 100 per chat)"
	case tgerr.Is(err, "SCORE_INVALID"):
		return "The specified game score is invalid"
	case tgerr.Is(err, "SEARCH_QUERY_EMPTY"):
		return "The search query is empty"
	case tgerr.Is(err, "SEARCH_WITH_LINK_NOT_SUPPORTED"):
		return "You cannot provide a search query and an invite link at the same time"
	case tgerr.Is(err, "SECONDS_INVALID"):
		return "Slow mode only supports certain values (e.g. 0, 10s, 30s, 1m, 5m, 15m and 1h)"
	case tgerr.Is(err, "SEND_AS_PEER_INVALID"):
		return "You can't send messages as the specified peer"
	case tgerr.Is(err, "SEND_CODE_UNAVAILABLE"):
		return "Returned when all available options for this type of number were already used (e.g. flash-call, then SMS, then this error might be returned to trigger a second resend)"
	case tgerr.Is(err, "SEND_MESSAGE_MEDIA_INVALID"):
		return "The message media was invalid or not specified"
	case tgerr.Is(err, "SEND_MESSAGE_TYPE_INVALID"):
		return "The message type is invalid"
	case tgerr.Is(err, "SENSITIVE_CHANGE_FORBIDDEN"):
		return "Your sensitive content settings cannot be changed at this time"
	case tgerr.Is(err, "SESSION_EXPIRED"):
		return "The authorization has expired"
	case tgerr.Is(err, "SESSION_PASSWORD_NEEDED"):
		return "Two-steps verification is enabled and a password is required"
	case tgerr.Is(err, "SESSION_REVOKED"):
		return "The authorization has been invalidated, because of the user terminating all sessions"
	case tgerr.Is(err, "SESSION_TOO_FRESH_X"):
		return "The session logged in too recently and {seconds} seconds must pass before calling the method"
	case tgerr.Is(err, "SETTINGS_INVALID"):
		return "Invalid settings were provided"
	case tgerr.Is(err, "SHA256_HASH_INVALID"):
		return "The provided SHA256 hash is invalid"
	case tgerr.Is(err, "SHORTNAME_OCCUPY_FAILED"):
		return "An error occurred when trying to register the short-name used for the sticker pack. Try a different name"
	case tgerr.Is(err, "SHORT_NAME_INVALID"):
		return "The specified short name is invalid"
	case tgerr.Is(err, "SHORT_NAME_OCCUPIED"):
		return "The specified short name is already in use"
	case tgerr.Is(err, "SIGN_IN_FAILED"):
		return "Failure while signing in"
	case tgerr.Is(err, "SLOWMODE_MULTI_MSGS_DISABLED"):
		return "Slowmode is enabled, you cannot forward multiple messages to this group"
	case tgerr.Is(err, "SLOWMODE_WAIT_X"):
		return "A wait of {seconds} seconds is required before sending another message in this chat"
	case tgerr.Is(err, "SMS_CODE_CREATE_FAILED"):
		return "An error occurred while creating the SMS code"
	case tgerr.Is(err, "SRP_ID_INVALID"):
		return "Invalid SRP ID provided"
	case tgerr.Is(err, "SRP_PASSWORD_CHANGED"):
		return "Password has changed"
	case tgerr.Is(err, "START_PARAM_EMPTY"):
		return "The start parameter is empty"
	case tgerr.Is(err, "START_PARAM_INVALID"):
		return "Start parameter invalid"
	case tgerr.Is(err, "START_PARAM_TOO_LONG"):
		return "Start parameter is too long"
	case tgerr.Is(err, "STATS_MIGRATE_X"):
		return "The channel statistics must be fetched from DC {dc}"
	case tgerr.Is(err, "STICKERPACK_STICKERS_TOO_MUCH"):
		return "There are too many stickers in this stickerpack, you can't add any more"
	case tgerr.Is(err, "STICKERSET_INVALID"):
		return "The provided sticker set is invalid"
	case tgerr.Is(err, "STICKERSET_OWNER_ANONYMOUS"):
		return "This sticker set can't be used as the group's official stickers because it was created by one of its anonymous admins"
	case tgerr.Is(err, "STICKERS_EMPTY"):
		return "No sticker provided"
	case tgerr.Is(err, "STICKERS_TOO_MUCH"):
		return "There are too many stickers in this stickerpack, you can't add any more"
	case tgerr.Is(err, "STICKER_DOCUMENT_INVALID"):
		return "The sticker file was invalid (this file has failed Telegram internal checks, make sure to use the correct format and comply with https://core.telegram.org/animated_stickers)"
	case tgerr.Is(err, "STICKER_EMOJI_INVALID"):
		return "Sticker emoji invalid"
	case tgerr.Is(err, "STICKER_FILE_INVALID"):
		return "Sticker file invalid"
	case tgerr.Is(err, "STICKER_GIF_DIMENSIONS"):
		return "The specified video sticker has invalid dimensions"
	case tgerr.Is(err, "STICKER_ID_INVALID"):
		return "The provided sticker ID is invalid"
	case tgerr.Is(err, "STICKER_INVALID"):
		return "The provided sticker is invalid"
	case tgerr.Is(err, "STICKER_MIME_INVALID"):
		return "Make sure to pass a valid image file for the right InputFile parameter"
	case tgerr.Is(err, "STICKER_PNG_DIMENSIONS"):
		return "Sticker png dimensions invalid"
	case tgerr.Is(err, "STICKER_PNG_NOPNG"):
		return "Stickers must be a png file but the used image was not a png"
	case tgerr.Is(err, "STICKER_TGS_NODOC"):
		return "You must send the animated sticker as a document"
	case tgerr.Is(err, "STICKER_TGS_NOTGS"):
		return "Stickers must be a tgs file but the used file was not a tgs"
	case tgerr.Is(err, "STICKER_THUMB_PNG_NOPNG"):
		return "Stickerset thumb must be a png file but the used file was not png"
	case tgerr.Is(err, "STICKER_THUMB_TGS_NOTGS"):
		return "Stickerset thumb must be a tgs file but the used file was not tgs"
	case tgerr.Is(err, "STICKER_VIDEO_BIG"):
		return "The specified video sticker is too big"
	case tgerr.Is(err, "STICKER_VIDEO_NODOC"):
		return "You must send the video sticker as a document"
	case tgerr.Is(err, "STICKER_VIDEO_NOWEBM"):
		return "The specified video sticker is not in webm format"
	case tgerr.Is(err, "STORAGE_CHECK_FAILED"):
		return "Server storage check failed"
	case tgerr.Is(err, "STORE_INVALID_SCALAR_TYPE"):
		return ""
	case tgerr.Is(err, "SWITCH_PM_TEXT_EMPTY"):
		return "The switch_pm.text field was empty"
	case tgerr.Is(err, "TAKEOUT_INIT_DELAY_X"):
		return "A wait of {seconds} seconds is required before being able to initiate the takeout"
	case tgerr.Is(err, "TAKEOUT_INVALID"):
		return "The takeout session has been invalidated by another data export session"
	case tgerr.Is(err, "TAKEOUT_REQUIRED"):
		return "You must initialize a takeout request first"
	case tgerr.Is(err, "TEMP_AUTH_KEY_ALREADY_BOUND"):
		return "The passed temporary key is already bound to another **perm_auth_key_id**"
	case tgerr.Is(err, "TEMP_AUTH_KEY_EMPTY"):
		return "No temporary auth key provided"
	case tgerr.Is(err, "THEME_FILE_INVALID"):
		return "Invalid theme file provided"
	case tgerr.Is(err, "THEME_FORMAT_INVALID"):
		return "Invalid theme format provided"
	case tgerr.Is(err, "THEME_INVALID"):
		return "Theme invalid"
	case tgerr.Is(err, "THEME_MIME_INVALID"):
		return "You cannot create this theme, the mime-type is invalid"
	case tgerr.Is(err, "THEME_TITLE_INVALID"):
		return "The specified theme title is invalid"
	case tgerr.Is(err, "TIMEOUT"):
		return "A timeout occurred while fetching data from the worker"
	case tgerr.Is(err, "TITLE_INVALID"):
		return "The specified stickerpack title is invalid"
	case tgerr.Is(err, "TMP_PASSWORD_DISABLED"):
		return "The temporary password is disabled"
	case tgerr.Is(err, "TMP_PASSWORD_INVALID"):
		return "Password auth needs to be regenerated"
	case tgerr.Is(err, "TOKEN_INVALID"):
		return "The provided token is invalid"
	case tgerr.Is(err, "TOPIC_DELETED"):
		return "The topic was deleted"
	case tgerr.Is(err, "TO_LANG_INVALID"):
		return "The specified destination language is invalid"
	case tgerr.Is(err, "TTL_DAYS_INVALID"):
		return "The provided TTL is invalid"
	case tgerr.Is(err, "TTL_MEDIA_INVALID"):
		return "The provided media cannot be used with a TTL"
	case tgerr.Is(err, "TTL_PERIOD_INVALID"):
		return "The provided TTL Period is invalid"
	case tgerr.Is(err, "TYPES_EMPTY"):
		return "The types field is empty"
	case tgerr.Is(err, "TYPE_CONSTRUCTOR_INVALID"):
		return "The type constructor is invalid"
	case tgerr.Is(err, "Timedout"):
		return "Timeout while fetching data"
	case tgerr.Is(err, "Timeout"):
		return "Timeout while fetching data"
	case tgerr.Is(err, "UNKNOWN_ERROR"):
		return ""
	case tgerr.Is(err, "UNKNOWN_METHOD"):
		return "The method you tried to call cannot be called on non-CDN DCs"
	case tgerr.Is(err, "UNTIL_DATE_INVALID"):
		return "That date cannot be specified in this request (try using None)"
	case tgerr.Is(err, "UPDATE_APP_TO_LOGIN"):
		return ""
	case tgerr.Is(err, "URL_INVALID"):
		return "The URL used was invalid (e.g. when answering a callback with a URL that's not t.me/yourbot or your game's URL)"
	case tgerr.Is(err, "USAGE_LIMIT_INVALID"):
		return "The specified usage limit is invalid"
	case tgerr.Is(err, "USERNAME_INVALID"):
		return "Nobody is using this username, or the username is unacceptable. If the latter, it must match r\"[a-zA-Z][\\w\\d]{3,30}[a-zA-Z\\d]\""
	case tgerr.Is(err, "USERNAME_NOT_MODIFIED"):
		return "The username is not different from the current username"
	case tgerr.Is(err, "USERNAME_NOT_OCCUPIED"):
		return "The username is not in use by anyone else yet"
	case tgerr.Is(err, "USERNAME_OCCUPIED"):
		return "The username is already taken"
	case tgerr.Is(err, "USERNAME_PURCHASE_AVAILABLE"):
		return ""
	case tgerr.Is(err, "USERPIC_PRIVACY_REQUIRED"):
		return "You need to disable privacy settings for your profile picture in order to make your geolocation public"
	case tgerr.Is(err, "USERPIC_UPLOAD_REQUIRED"):
		return "You must have a profile picture before using this method"
	case tgerr.Is(err, "USERS_TOO_FEW"):
		return "Not enough users (to create a chat, for example)"
	case tgerr.Is(err, "USERS_TOO_MUCH"):
		return "The maximum number of users has been exceeded (to create a chat, for example)"
	case tgerr.Is(err, "USER_ADMIN_INVALID"):
		return "Either you're not an admin or you tried to ban an admin that you didn't promote"
	case tgerr.Is(err, "USER_ALREADY_INVITED"):
		return "You have already invited this user"
	case tgerr.Is(err, "USER_ALREADY_PARTICIPANT"):
		return "The authenticated user is already a participant of the chat"
	case tgerr.Is(err, "USER_BANNED_IN_CHANNEL"):
		return "You're banned from sending messages in supergroups/channels"
	case tgerr.Is(err, "USER_BLOCKED"):
		return "User blocked"
	case tgerr.Is(err, "USER_BOT"):
		return "Bots can only be admins in channels."
	case tgerr.Is(err, "USER_BOT_INVALID"):
		return "This method can only be called by a bot"
	case tgerr.Is(err, "USER_BOT_REQUIRED"):
		return "This method can only be called by a bot"
	case tgerr.Is(err, "USER_CHANNELS_TOO_MUCH"):
		return "One of the users you tried to add is already in too many channels/supergroups"
	case tgerr.Is(err, "USER_CREATOR"):
		return "You can't leave this channel, because you're its creator"
	case tgerr.Is(err, "USER_DEACTIVATED"):
		return "The user has been deleted/deactivated"
	case tgerr.Is(err, "USER_DEACTIVATED_BAN"):
		return "The user has been deleted/deactivated"
	case tgerr.Is(err, "USER_DELETED"):
		return "You can't send this secret message because the other participant deleted their account"
	case tgerr.Is(err, "USER_ID_INVALID"):
		return "Invalid object ID for a user. Make sure to pass the right types, for instance making sure that the request is designed for users or otherwise look for a different one more suited"
	case tgerr.Is(err, "USER_INVALID"):
		return "The given user was invalid"
	case tgerr.Is(err, "USER_IS_BLOCKED"):
		return "User is blocked"
	case tgerr.Is(err, "USER_IS_BOT"):
		return "Bots can't send messages to other bots"
	case tgerr.Is(err, "USER_KICKED"):
		return "This user was kicked from this supergroup/channel"
	case tgerr.Is(err, "USER_MIGRATE_X"):
		return "The user whose identity is being used to execute queries is associated with DC {new_dc}"
	case tgerr.Is(err, "USER_NOT_MUTUAL_CONTACT"):
		return "The provided user is not a mutual contact"
	case tgerr.Is(err, "USER_NOT_PARTICIPANT"):
		return "The target user is not a member of the specified megagroup or channel"
	case tgerr.Is(err, "USER_PRIVACY_RESTRICTED"):
		return "The user's privacy settings do not allow you to do this"
	case tgerr.Is(err, "USER_RESTRICTED"):
		return "You're spamreported, you can't create channels or chats."
	case tgerr.Is(err, "USER_VOLUME_INVALID"):
		return "The specified user volume is invalid"
	case tgerr.Is(err, "VIDEO_CONTENT_TYPE_INVALID"):
		return "The video content type is not supported with the given parameters (i.e. supports_streaming)"
	case tgerr.Is(err, "VIDEO_FILE_INVALID"):
		return "The given video cannot be used"
	case tgerr.Is(err, "VIDEO_TITLE_EMPTY"):
		return "The specified video title is empty"
	case tgerr.Is(err, "VOICE_MESSAGES_FORBIDDEN"):
		return "This user's privacy settings forbid you from sending voice messages"
	case tgerr.Is(err, "WALLPAPER_FILE_INVALID"):
		return "The given file cannot be used as a wallpaper"
	case tgerr.Is(err, "WALLPAPER_INVALID"):
		return "The input wallpaper was not valid"
	case tgerr.Is(err, "WALLPAPER_MIME_INVALID"):
		return "The specified wallpaper MIME type is invalid"
	case tgerr.Is(err, "WC_CONVERT_URL_INVALID"):
		return "WC convert URL invalid"
	case tgerr.Is(err, "WEBDOCUMENT_INVALID"):
		return "Invalid webdocument URL provided"
	case tgerr.Is(err, "WEBDOCUMENT_MIME_INVALID"):
		return "Invalid webdocument mime type provided"
	case tgerr.Is(err, "WEBDOCUMENT_SIZE_TOO_BIG"):
		return "Webdocument is too big!"
	case tgerr.Is(err, "WEBDOCUMENT_URL_INVALID"):
		return "The given URL cannot be used"
	case tgerr.Is(err, "WEBPAGE_CURL_FAILED"):
		return "Failure while fetching the webpage with cURL"
	case tgerr.Is(err, "WEBPAGE_MEDIA_EMPTY"):
		return "Webpage media empty"
	case tgerr.Is(err, "WEBPUSH_AUTH_INVALID"):
		return "The specified web push authentication secret is invalid"
	case tgerr.Is(err, "WEBPUSH_KEY_INVALID"):
		return "The specified web push elliptic curve Diffie-Hellman public key is invalid"
	case tgerr.Is(err, "WEBPUSH_TOKEN_INVALID"):
		return "The specified web push token is invalid"
	case tgerr.Is(err, "WORKER_BUSY_TOO_LONG_RETRY"):
		return "Telegram workers are too busy to respond immediately"
	case tgerr.Is(err, "YOU_BLOCKED_USER"):
		return "You blocked this user"
	}
	return err.Error()
}
