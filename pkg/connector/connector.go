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
	"strconv"

	"github.com/gotd/td/telegram"
	"github.com/rs/zerolog"
	"go.mau.fi/util/dbutil"
	"go.mau.fi/zerozap"
	"go.uber.org/zap"
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

func (tg *TelegramConnector) LoadUserLogin(ctx context.Context, login *bridgev2.UserLogin) error {
	loginID, err := strconv.ParseInt(string(login.ID), 10, 64)
	if err != nil {
		return err
	}

	logger := zerolog.Ctx(ctx).With().
		Str("component", "telegram_client").
		Int64("login_id", loginID).
		Logger()

	login.Client = &TelegramClient{
		main:      tg,
		userLogin: login,
		client: telegram.NewClient(tg.Config.AppID, tg.Config.AppHash, telegram.Options{
			SessionStorage: tg.store.GetSessionStore(loginID),
			Logger:         zap.New(zerozap.New(logger)),
		}),
	}
	return nil
}
