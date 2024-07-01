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

package store

import (
	"context"

	"go.mau.fi/util/dbutil"

	"go.mau.fi/mautrix-telegram/pkg/store/upgrades"
)

type Container struct {
	*dbutil.Database

	TelegramFile *TelegramFileQuery
}

func NewStore(db *dbutil.Database, log dbutil.DatabaseLogger) *Container {
	return &Container{
		Database: db.Child("telegram_version", upgrades.Table, log),

		TelegramFile: &TelegramFileQuery{dbutil.MakeQueryHelper(db, newTelegramFile)},
	}
}

func (c *Container) Upgrade(ctx context.Context) error {
	return c.Database.Upgrade(ctx)
}

func (c *Container) GetScopedStore(telegramUserID int64) *scopedStore {
	return &scopedStore{c.Database, telegramUserID}
}
