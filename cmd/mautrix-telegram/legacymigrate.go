// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2025 Sumner Evans
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

package main

import (
	"context"
	_ "embed"
	"fmt"

	"github.com/rs/zerolog"
	up "go.mau.fi/util/configupgrade"
	"go.mau.fi/util/dbutil"
	"maunium.net/go/mautrix/bridgev2/bridgeconfig"
)

const legacyMigrateRenameTablesQuery = `
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
DROP INDEX IF EXISTS telegram_file_mxc_idx;
`

func legacyMigrateRenameTables(ctx context.Context, db *dbutil.Database) error {
	_, err := db.Exec(ctx, legacyMigrateRenameTablesQuery)
	if err != nil {
		return err
	}
	var mxVersion int
	err = db.QueryRow(ctx, "SELECT version FROM mx_version").Scan(&mxVersion)
	if err != nil {
		return fmt.Errorf("failed to get mx_version: %w", err)
	} else if mxVersion == 3 {
		zerolog.Ctx(ctx).Debug().Msg("mx_version is 3, adding create_event column before running actual migration")
		_, err = db.Exec(ctx, `
			ALTER TABLE mx_room_state ADD COLUMN create_event TEXT;
			UPDATE mx_version SET version=4;
		`)
		if err != nil {
			return fmt.Errorf("failed to add create_event column to mx_room_state: %w", err)
		}
	}
	return nil
}

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
	bridgeconfig.CopyToOtherLocation(helper, up.Str, []string{"telegram", "proxy", "type"}, []string{"network", "proxy", "type"})
	proxyAddress, _ := helper.Get(up.Str, "telegram", "proxy", "address")
	proxyPort, _ := helper.Get(up.Int, "telegram", "proxy", "port")
	helper.Set(up.Str, fmt.Sprintf("%s:%s", proxyAddress, proxyPort), "network", "proxy", "address")
	bridgeconfig.CopyToOtherLocation(helper, up.Str, []string{"telegram", "proxy", "username"}, []string{"network", "proxy", "username"})
	bridgeconfig.CopyToOtherLocation(helper, up.Str, []string{"telegram", "proxy", "password"}, []string{"network", "proxy", "password"})
	bridgeconfig.CopyToOtherLocation(helper, up.Int, []string{"bridge", "max_member_count"}, []string{"network", "max_member_count"})
	bridgeconfig.CopyToOtherLocation(helper, up.Int, []string{"bridge", "sync_update_limit"}, []string{"network", "sync", "update_limit"})
	bridgeconfig.CopyToOtherLocation(helper, up.Int, []string{"bridge", "sync_create_limit"}, []string{"network", "sync", "create_limit"})
	bridgeconfig.CopyToOtherLocation(helper, up.Bool, []string{"bridge", "sync_direct_chats"}, []string{"network", "sync", "direct_chats"})
	bridgeconfig.CopyToOtherLocation(helper, up.Bool, []string{"bridge", "always_custom_emoji_reaction"}, []string{"network", "always_custom_emoji_reaction"})
}
