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

package connector

import (
	_ "embed"
	"fmt"
	"slices"
	"strings"
	"text/template"

	up "go.mau.fi/util/configupgrade"
	"gopkg.in/yaml.v3"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/bridgeconfig"
	"maunium.net/go/mautrix/id"

	"go.mau.fi/mautrix-telegram/pkg/connector/media"
)

var _ bridgev2.ConfigValidatingNetwork = (*TelegramConnector)(nil)

type MemberListConfig struct {
	MaxInitialSync        int  `yaml:"max_initial_sync"`
	SyncBroadcastChannels bool `yaml:"sync_broadcast_channels"`
	SkipDeleted           bool `yaml:"skip_deleted"`
}

func (c MemberListConfig) NormalizedMaxInitialSync() int {
	if c.MaxInitialSync < 0 {
		return 10_000
	}
	return c.MaxInitialSync
}

type DeviceInfo struct {
	DeviceModel    string `yaml:"device_model"`
	SystemVersion  string `yaml:"system_version"`
	AppVersion     string `yaml:"app_version"`
	SystemLangCode string `yaml:"system_lang_code"`
	LangCode       string `yaml:"lang_code"`
}

type ProxyConfig struct {
	Type     string `yaml:"type"`
	Address  string `yaml:"address"`
	Username string `yaml:"username"`
	Password string `yaml:"password"`
}

type TelegramConfig struct {
	APIID   int    `yaml:"api_id"`
	APIHash string `yaml:"api_hash"`

	DeviceInfo      DeviceInfo                  `yaml:"device_info"`
	AnimatedSticker media.AnimatedStickerConfig `yaml:"animated_sticker"`
	MemberList      MemberListConfig            `yaml:"member_list"`

	Ping struct {
		IntervalSeconds int `yaml:"interval_seconds"`
		TimeoutSeconds  int `yaml:"timeout_seconds"`
	} `yaml:"ping"`

	ProxyConfig ProxyConfig `yaml:"proxy"`

	Sync struct {
		UpdateLimit int  `yaml:"update_limit"`
		CreateLimit int  `yaml:"create_limit"`
		LoginLimit  int  `yaml:"login_sync_limit"`
		DirectChats bool `yaml:"direct_chats"`
	} `yaml:"sync"`

	Takeout struct {
		DialogSync       bool `yaml:"dialog_sync"`
		ForwardBackfill  bool `yaml:"forward_backfill"`
		BackwardBackfill bool `yaml:"backward_backfill"`
	} `yaml:"takeout"`

	ContactAvatars                       bool                `yaml:"contact_avatars"`
	ContactNames                         bool                `yaml:"contact_names"`
	MaxMemberCount                       int                 `yaml:"max_member_count"`
	AlwaysCustomEmojiReaction            bool                `yaml:"always_custom_emoji_reaction"`
	SavedMessagesAvatar                  id.ContentURIString `yaml:"saved_message_avatar"`
	AlwaysTombstoneOnSupergroupMigration bool                `yaml:"always_tombstone_on_supergroup_migration"`
	ImageAsFilePixels                    int                 `yaml:"image_as_file_pixels"`
	DisableViewOnce                      bool                `yaml:"disable_view_once"`
	DisplaynameTemplate                  string              `yaml:"displayname_template"`
	displaynameTemplate                  *template.Template  `yaml:"-"`
}

func (c TelegramConfig) ShouldBridge(participantCount int) bool {
	return c.MaxMemberCount < 0 || participantCount <= c.MaxMemberCount
}

type DisplaynameParams struct {
	FullName  string
	FirstName string
	LastName  string
	Username  string
	UserID    int64
	Deleted   bool
}

func (c *TelegramConfig) FormatDisplayname(firstName, lastName, username string, deleted bool, userID int64) string {
	var buf strings.Builder
	err := c.displaynameTemplate.Execute(&buf, DisplaynameParams{
		FullName:  strings.TrimSpace(firstName + " " + lastName),
		FirstName: firstName,
		LastName:  lastName,
		Username:  username,
		UserID:    userID,
		Deleted:   deleted,
	})
	if err != nil {
		panic(fmt.Errorf("displayname template is broken: %w", err))
	}
	return buf.String()
}

type umConfig TelegramConfig

func (c *TelegramConfig) UnmarshalYAML(node *yaml.Node) error {
	err := node.Decode((*umConfig)(c))
	if err != nil {
		return err
	}
	return c.PostProcess()
}

func (c *TelegramConfig) PostProcess() error {
	var err error
	c.displaynameTemplate, err = template.New("displayname").Parse(c.DisplaynameTemplate)
	return err
}

//go:embed example-config.yaml
var ExampleConfig string

func upgradeConfig(helper up.Helper) {
	bridgeconfig.CopyToOtherLocation(helper, up.Int, []string{"app_id"}, []string{"api_id"})
	bridgeconfig.CopyToOtherLocation(helper, up.Str, []string{"app_hash"}, []string{"api_hash"})
	helper.Copy(up.Int, "api_id")
	helper.Copy(up.Str, "api_hash")
	helper.Copy(up.Str|up.Null, "device_info", "device_model")
	helper.Copy(up.Str|up.Null, "device_info", "system_version")
	helper.Copy(up.Str|up.Null, "device_info", "app_version")
	helper.Copy(up.Str|up.Null, "device_info", "system_lang_code")
	helper.Copy(up.Str|up.Null, "device_info", "lang_code")
	helper.Copy(up.Str, "animated_sticker", "target")
	helper.Copy(up.Bool, "animated_sticker", "convert_from_webm")
	helper.Copy(up.Int, "animated_sticker", "args", "width")
	helper.Copy(up.Int, "animated_sticker", "args", "height")
	helper.Copy(up.Int, "animated_sticker", "args", "fps")
	helper.Copy(up.Int, "member_list", "max_initial_sync")
	helper.Copy(up.Bool, "member_list", "sync_broadcast_channels")
	helper.Copy(up.Bool, "member_list", "skip_deleted")
	helper.Copy(up.Int, "ping", "interval_seconds")
	helper.Copy(up.Int, "ping", "timeout_seconds")
	helper.Copy(up.Str, "proxy", "type")
	helper.Copy(up.Str, "proxy", "address")
	helper.Copy(up.Str, "proxy", "username")
	helper.Copy(up.Str, "proxy", "password")
	helper.Copy(up.Int, "sync", "update_limit")
	helper.Copy(up.Int, "sync", "create_limit")
	helper.Copy(up.Int, "sync", "login_sync_limit")
	helper.Copy(up.Bool, "sync", "direct_chats")
	helper.Copy(up.Bool, "takeout", "dialog_sync")
	helper.Copy(up.Bool, "takeout", "forward_backfill")
	helper.Copy(up.Bool, "takeout", "backward_backfill")
	helper.Copy(up.Bool, "contact_avatars")
	helper.Copy(up.Bool, "contact_names")
	helper.Copy(up.Int, "max_member_count")
	helper.Copy(up.Bool, "always_custom_emoji_reaction")
	helper.Copy(up.Str, "saved_message_avatar")
	helper.Copy(up.Bool, "always_tombstone_on_supergroup_migration")
	helper.Copy(up.Int, "image_as_file_pixels")
	helper.Copy(up.Bool, "disable_view_once")
	helper.Copy(up.Str, "displayname_template")
}

func (tc *TelegramConnector) GetConfig() (example string, data any, upgrader up.Upgrader) {
	return ExampleConfig, &tc.Config, &up.StructUpgrader{
		SimpleUpgrader: up.SimpleUpgrader(upgradeConfig),
		Blocks: [][]string{
			{"device_info"},
			{"animated_sticker"},
			{"member_list"},
			{"ping"},
			{"proxy"},
			{"sync"},
			{"takeout"},
			{"max_member_count"},
		},
		Base: ExampleConfig,
	}
}

func (tc *TelegramConnector) ValidateConfig() error {
	if tc.Config.APIID == 0 {
		return fmt.Errorf("api_id is required")
	}
	if tc.Config.APIHash == "" || tc.Config.APIHash == "tjyd5yge35lbodk1xwzw2jstp90k55qz" {
		return fmt.Errorf("api_hash is required")
	}
	if !slices.Contains([]string{"disable", "gif", "png", "webp", "webm"}, tc.Config.AnimatedSticker.Target) {
		return fmt.Errorf("unsupported animated sticker target: %s", tc.Config.AnimatedSticker.Target)
	}
	return nil
}
