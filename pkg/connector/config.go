package connector

import (
	_ "embed"
	"fmt"
	"slices"

	up "go.mau.fi/util/configupgrade"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"

	"go.mau.fi/mautrix-telegram/pkg/connector/media"
)

var _ bridgev2.ConfigValidatingNetwork = (*TelegramConnector)(nil)

type TelegramConfig struct {
	AppID   int    `yaml:"app_id"`
	AppHash string `yaml:"app_hash"`

	SetPrivateChatPortalMeta bool `yaml:"set_private_chat_portal_meta"`

	AnimatedSticker media.AnimatedStickerConfig `yaml:"animated_sticker"`

	MemberList struct {
		MaxInitialSync int  `yaml:"max_initial_sync"`
		SyncChannels   bool `yaml:"sync_channels"`
		SkipDeleted    bool `yaml:"skip_deleted"`
	} `yaml:"member_list"`

	MaxMemberCount int `yaml:"max_member_count"`
}

//go:embed example-config.yaml
var ExampleConfig string

func upgradeConfig(helper up.Helper) {
	helper.Copy(up.Int, "app_id")
	helper.Copy(up.Str, "app_hash")
	helper.Copy(up.Bool, "set_private_chat_portal_meta")
	helper.Copy(up.Str, "animated_sticker", "target")
	helper.Copy(up.Bool, "animated_sticker", "convert_from_webm")
	helper.Copy(up.Int, "animated_sticker", "args", "width")
	helper.Copy(up.Int, "animated_sticker", "args", "height")
	helper.Copy(up.Int, "animated_sticker", "args", "fps")
	helper.Copy(up.Int, "member_list", "max_initial_sync")
	helper.Copy(up.Bool, "member_list", "sync_channels")
	helper.Copy(up.Bool, "member_list", "skip_deleted")
	helper.Copy(up.Int, "max_member_count")
}

func (tg *TelegramConnector) GetConfig() (example string, data any, upgrader up.Upgrader) {
	return ExampleConfig, tg.Config, up.SimpleUpgrader(upgradeConfig)
}

func (tg *TelegramConnector) ValidateConfig() error {
	if tg.Config.AppID == 0 {
		return fmt.Errorf("app_id is required")
	}
	if tg.Config.AppHash == "" {
		return fmt.Errorf("app_hash is required")
	}
	if !slices.Contains([]string{"disable", "gif", "png", "webp", "webm"}, tg.Config.AnimatedSticker.Target) {
		return fmt.Errorf("unsupported animated sticker target: %s", tg.Config.AnimatedSticker.Target)
	}
	return nil
}

func (tg *TelegramConnector) GetDBMetaTypes() database.MetaTypes {
	return database.MetaTypes{
		Ghost: func() any {
			return &GhostMetadata{}
		},
		Portal:   nil,
		Message:  nil,
		Reaction: nil,
		UserLogin: func() any {
			return &UserLoginMetadata{}
		},
	}
}

type GhostMetadata struct {
	IsPremium  bool  `json:"is_premium"`
	AccessHash int64 `json:"access_hash"`
}

type UserLoginMetadata struct {
	Phone string `json:"phone"`
}
