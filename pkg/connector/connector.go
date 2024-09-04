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

	"go.mau.fi/util/dbutil"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/commands"

	"go.mau.fi/mautrix-telegram/pkg/connector/store"
)

type TelegramConnector struct {
	Bridge *bridgev2.Bridge
	Config *TelegramConfig
	Store  *store.Container

	useDirectMedia bool
	maxFileSize    int64
}

var _ bridgev2.NetworkConnector = (*TelegramConnector)(nil)
var _ bridgev2.MaxFileSizeingNetwork = (*TelegramConnector)(nil)

func (tg *TelegramConnector) Init(bridge *bridgev2.Bridge) {
	tg.Store = store.NewStore(bridge.DB.Database, dbutil.ZeroLogger(bridge.Log.With().Str("db_section", "telegram").Logger()))
	tg.Bridge = bridge
	tg.Bridge.Commands.(*commands.Processor).AddHandlers(cmdSync)
}

func (tg *TelegramConnector) Start(ctx context.Context) error {
	return tg.Store.Upgrade(ctx)
}

func (tc *TelegramConnector) LoadUserLogin(ctx context.Context, login *bridgev2.UserLogin) (err error) {
	login.Client, err = NewTelegramClient(ctx, tc, login)
	return
}

func (tg *TelegramConnector) SetMaxFileSize(maxSize int64) {
	tg.maxFileSize = maxSize
}

func (tg *TelegramConnector) GetName() bridgev2.BridgeName {
	return bridgev2.BridgeName{
		DisplayName:          "Telegram",
		NetworkURL:           "https://telegram.org/",
		NetworkIcon:          "mxc://maunium.net/tJCRmUyJDsgRNgqhOgoiHWbX",
		NetworkID:            "telegram",
		BeeperBridgeType:     "telegram",
		DefaultPort:          29317,
		DefaultCommandPrefix: "!tg",
	}
}

func (tg *TelegramConnector) GetCapabilities() *bridgev2.NetworkGeneralCapabilities {
	return &bridgev2.NetworkGeneralCapabilities{
		DisappearingMessages: true,
	}
}
