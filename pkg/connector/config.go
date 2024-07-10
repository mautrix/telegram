package connector

import (
	_ "embed"
	"fmt"
	"slices"

	up "go.mau.fi/util/configupgrade"
	"maunium.net/go/mautrix/bridgev2"

	"go.mau.fi/mautrix-telegram/pkg/connector/media"
)

var _ bridgev2.ConfigValidatingNetwork = (*TelegramConnector)(nil)

type TelegramConfig struct {
	AppID   int    `yaml:"app_id"`
	AppHash string `yaml:"app_hash"`

	AnimatedSticker media.AnimatedStickerConfig `yaml:"animated_sticker"`
}

//go:embed example-config.yaml
var ExampleConfig string

func upgradeConfig(helper up.Helper) {
	helper.Copy(up.Int, "app_id")
	helper.Copy(up.Str, "app_hash")
	helper.Copy(up.Str, "animated_sticker", "target")
	helper.Copy(up.Bool, "animated_sticker", "convert_from_webm")
	helper.Copy(up.Int, "animated_sticker", "args", "width")
	helper.Copy(up.Int, "animated_sticker", "args", "height")
	helper.Copy(up.Int, "animated_sticker", "args", "fps")
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
