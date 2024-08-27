package main

import (
	_ "embed"

	up "go.mau.fi/util/configupgrade"
	"maunium.net/go/mautrix/bridgev2/bridgeconfig"
)

const legacyMigrateRenameTables = `
ALTER TABLE backfill_queue RENAME TO backfill_queue_old;
ALTER TABLE bot_chat RENAME TO bot_chat_old;
ALTER TABLE contact RENAME TO contact_old;
ALTER TABLE disappearing_message RENAME TO disappearing_message_old;
ALTER TABLE message RENAME TO message_old;
ALTER TABLE portal RENAME TO portal_old;
ALTER TABLE puppet RENAME TO puppet_old;
ALTER TABLE reaction RENAME TO reaction_old;
ALTER TABLE telegram_file RENAME TO telegram_file_old;
ALTER TABLE telethon_entities RENAME TO telethon_entities_old;
ALTER TABLE telethon_sent_files RENAME TO telethon_sent_files_old;
ALTER TABLE telethon_sessions RENAME TO telethon_sessions_old;
ALTER TABLE telethon_update_state RENAME TO telethon_update_state_old;
ALTER TABLE "user" RENAME TO user_old;
ALTER TABLE user_portal RENAME TO user_portal_old;
`

//go:embed legacymigrate.sql
var legacyMigrateCopyData string

func migrateLegacyConfig(helper up.Helper) {
	helper.Set(up.Str, "mautrix.bridge.e2ee", "encryption", "pickle_key")
	bridgeconfig.CopyToOtherLocation(helper, up.Int, []string{"telegram", "api_id"}, []string{"network", "api_id"})
	bridgeconfig.CopyToOtherLocation(helper, up.Str, []string{"telegram", "api_hash"}, []string{"network", "api_hash"})
	bridgeconfig.CopyToOtherLocation(helper, up.Str, []string{"telegram", "device_info", "device_model"}, []string{"network", "device_info", "device_model"})
	bridgeconfig.CopyToOtherLocation(helper, up.Str, []string{"telegram", "device_info", "system_version"}, []string{"network", "device_info", "system_version"})
	bridgeconfig.CopyToOtherLocation(helper, up.Str, []string{"telegram", "device_info", "app_version"}, []string{"network", "device_info", "app_version"})
	bridgeconfig.CopyToOtherLocation(helper, up.Str, []string{"telegram", "device_info", "lang_code"}, []string{"network", "device_info", "lang_code"})
	bridgeconfig.CopyToOtherLocation(helper, up.Str, []string{"telegram", "device_info", "system_lang_code"}, []string{"network", "device_info", "system_lang_code"})
	bridgeconfig.CopyToOtherLocation(helper, up.Str, []string{"bridge", "animated_sticker", "target"}, []string{"network", "animated_sticker", "target"})
	bridgeconfig.CopyToOtherLocation(helper, up.Bool, []string{"bridge", "animated_sticker", "convert_from_webm"}, []string{"network", "animated_sticker", "convert_from_webm"})
	bridgeconfig.CopyToOtherLocation(helper, up.Int, []string{"bridge", "animated_sticker", "width"}, []string{"network", "animated_sticker", "width"})
	bridgeconfig.CopyToOtherLocation(helper, up.Int, []string{"bridge", "animated_sticker", "height"}, []string{"network", "animated_sticker", "height"})
	bridgeconfig.CopyToOtherLocation(helper, up.Int, []string{"bridge", "animated_sticker", "fps"}, []string{"network", "animated_sticker", "fps"})
	bridgeconfig.CopyToOtherLocation(helper, up.Int, []string{"bridge", "max_initial_member_sync"}, []string{"network", "member_list", "max_initial_sync"})
	bridgeconfig.CopyToOtherLocation(helper, up.Bool, []string{"bridge", "sync_channel_members"}, []string{"network", "member_list", "sync_broadcast_channels"})
	bridgeconfig.CopyToOtherLocation(helper, up.Bool, []string{"bridge", "skip_deleted_members"}, []string{"network", "member_list", "skip_deleted"})
	bridgeconfig.CopyToOtherLocation(helper, up.Int, []string{"bridge", "max_member_count"}, []string{"network", "max_member_count"})
	bridgeconfig.CopyToOtherLocation(helper, up.Int, []string{"bridge", "sync_update_limit"}, []string{"network", "sync", "update_limit"})
	bridgeconfig.CopyToOtherLocation(helper, up.Int, []string{"bridge", "sync_create_limit"}, []string{"network", "sync", "create_limit"})
	bridgeconfig.CopyToOtherLocation(helper, up.Bool, []string{"bridge", "sync_direct_chats"}, []string{"network", "sync", "direct_chats"})
}
