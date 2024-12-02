package connector

import (
	_ "embed"
	"fmt"
	"slices"

	up "go.mau.fi/util/configupgrade"
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

type TelegramConfig struct {
	APIID   int    `yaml:"api_id"`
	APIHash string `yaml:"api_hash"`

	DeviceInfo DeviceInfo `yaml:"device_info"`

	AnimatedSticker   media.AnimatedStickerConfig `yaml:"animated_sticker"`
	ImageAsFilePixels int                         `yaml:"image_as_file_pixels"`

	DisableViewOnce     bool `yaml:"disable_view_once"`
	DisableDisappearing bool `yaml:"disable_disappearing"`

	MemberList MemberListConfig `yaml:"member_list"`

	MaxMemberCount int `yaml:"max_member_count"`

	Ping struct {
		IntervalSeconds int `yaml:"interval_seconds"`
		TimeoutSeconds  int `yaml:"timeout_seconds"`
	} `yaml:"ping"`

	Sync struct {
		UpdateLimit int  `yaml:"update_limit"`
		CreateLimit int  `yaml:"create_limit"`
		DirectChats bool `yaml:"direct_chats"`
	} `yaml:"sync"`

	AlwaysCustomEmojiReaction bool `yaml:"always_custom_emoji_reaction"`

	SavedMessagesAvatar id.ContentURIString `yaml:"saved_message_avatar"`
}

func (c TelegramConfig) ShouldBridge(participantCount int) bool {
	return c.MaxMemberCount < 0 || participantCount <= c.MaxMemberCount
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
	helper.Copy(up.Int, "image_as_file_pixels")
	helper.Copy(up.Bool, "disable_view_once")
	helper.Copy(up.Bool, "disable_disappearing")
	helper.Copy(up.Int, "member_list", "max_initial_sync")
	helper.Copy(up.Bool, "member_list", "sync_broadcast_channels")
	helper.Copy(up.Bool, "member_list", "skip_deleted")
	helper.Copy(up.Int, "max_member_count")
	helper.Copy(up.Int, "ping", "interval_seconds")
	helper.Copy(up.Int, "ping", "timeout_seconds")
	helper.Copy(up.Int, "sync", "update_limit")
	helper.Copy(up.Int, "sync", "create_limit")
	helper.Copy(up.Bool, "sync", "direct_chats")
	helper.Copy(up.Bool, "always_custom_emoji_reaction")
	helper.Copy(up.Str, "saved_message_avatar")
}

func (tg *TelegramConnector) GetConfig() (example string, data any, upgrader up.Upgrader) {
	return ExampleConfig, &tg.Config, &up.StructUpgrader{
		SimpleUpgrader: up.SimpleUpgrader(upgradeConfig),
		Blocks: [][]string{
			{"device_info"},
			{"animated_sticker"},
			{"member_list"},
			{"ping"},
			{"sync"},
		},
		Base: ExampleConfig,
	}
}

func (tg *TelegramConnector) ValidateConfig() error {
	if tg.Config.APIID == 0 {
		return fmt.Errorf("api_id is required")
	}
	if tg.Config.APIHash == "" || tg.Config.APIHash == "tjyd5yge35lbodk1xwzw2jstp90k55qz" {
		return fmt.Errorf("api_hash is required")
	}
	if !slices.Contains([]string{"disable", "gif", "png", "webp", "webm"}, tg.Config.AnimatedSticker.Target) {
		return fmt.Errorf("unsupported animated sticker target: %s", tg.Config.AnimatedSticker.Target)
	}
	return nil
}
