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
	db *dbutil.Database
}

func NewStore(db *dbutil.Database, log dbutil.DatabaseLogger) *Container {
	return &Container{db: db.Child("telegram_version", upgrades.Table, log)}
}

func (c *Container) Upgrade(ctx context.Context) error {
	return c.db.Upgrade(ctx)
}

func (c *Container) GetSessionStore(telegramUserID int64) *SessionStore {
	return &SessionStore{c.db, telegramUserID}
}
