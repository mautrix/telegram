// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2024 Sumner Evans
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
	"context"
	_ "embed"
	"fmt"

	up "go.mau.fi/util/configupgrade"
	"go.mau.fi/util/dbutil"
	"maunium.net/go/mautrix/bridgev2"

	"go.mau.fi/mautrix-telegram/pkg/store"
)

type TelegramConfig struct {
	AppID   int    `yaml:"app_id"`
	AppHash string `yaml:"app_hash"`
}

type TelegramConnector struct {
	Bridge *bridgev2.Bridge
	Config *TelegramConfig

	store *store.Container
}

var _ bridgev2.NetworkConnector = (*TelegramConnector)(nil)
var _ bridgev2.ConfigValidatingNetwork = (*TelegramConnector)(nil)

// var _ bridgev2.MaxFileSizeingNetwork = (*TelegramConnector)(nil)

func NewConnector() *TelegramConnector {
	return &TelegramConnector{
		Config: &TelegramConfig{},
	}
}

func (tg *TelegramConnector) Init(bridge *bridgev2.Bridge) {
	// TODO
	tg.store = store.NewStore(bridge.DB.Database, dbutil.ZeroLogger(bridge.Log.With().Str("db_section", "telegram").Logger()))
	tg.Bridge = bridge
}

func (tg *TelegramConnector) Start(ctx context.Context) error {
	return tg.store.Upgrade(ctx)
}

func (tc *TelegramConnector) LoadUserLogin(ctx context.Context, login *bridgev2.UserLogin) (err error) {
	login.Client, err = NewTelegramClient(ctx, tc, login)
	return
}

//go:embed example-config.yaml
var ExampleConfig string

func upgradeConfig(helper up.Helper) {
	helper.Copy(up.Int, "app_id")
	helper.Copy(up.Str, "app_hash")
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
	return nil
}

// TODO
// func (tg *TelegramConnector) SetMaxFileSize(maxSize int64) {
// }

func (tg *TelegramConnector) GetName() bridgev2.BridgeName {
	return bridgev2.BridgeName{
		DisplayName:      "Telegram",
		NetworkURL:       "https://telegram.org/",
		NetworkIcon:      "mxc://maunium.net/tJCRmUyJDsgRNgqhOgoiHWbX",
		NetworkID:        "telegram",
		BeeperBridgeType: "telegram",
		DefaultPort:      29317,
	}
}
