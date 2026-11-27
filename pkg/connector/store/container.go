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
	"database/sql"
	"errors"
	"time"

	"go.mau.fi/util/dbutil"
	"go.mau.fi/util/exsync"
	"maunium.net/go/mautrix/bridgev2/networkid"

	"go.mau.fi/mautrix-telegram/pkg/connector/store/upgrades"
)

type Container struct {
	*dbutil.Database

	TelegramFile *TelegramFileQuery
	Username     *UsernameQuery
	PhoneNumber  *PhoneNumberQuery
	Topic        *TopicQuery
}

func NewStore(db *dbutil.Database, log dbutil.DatabaseLogger) *Container {
	return &Container{
		Database: db.Child("telegram_version", upgrades.Table, log),

		TelegramFile: &TelegramFileQuery{dbutil.MakeQueryHelper(db, newTelegramFile)},
		Username:     &UsernameQuery{db},
		PhoneNumber:  &PhoneNumberQuery{db},
		Topic:        &TopicQuery{db: db, existingTopics: exsync.NewSet[topicKey]()},
	}
}

func (c *Container) Upgrade(ctx context.Context) error {
	return c.Database.Upgrade(ctx)
}

func (c *Container) GetScopedStore(telegramUserID int64) *ScopedStore {
	return &ScopedStore{c.Database, telegramUserID}
}

const getLastTelegramMessageIDQuery = `
	SELECT id FROM message
	WHERE bridge_id=$1 AND room_id=$2 AND room_receiver=$3 AND timestamp<=$4 AND id NOT LIKE '%:%'
	ORDER BY timestamp DESC, LENGTH(id) DESC, id DESC
	LIMIT 1
`

func (c *Container) GetLastTelegramMessageID(ctx context.Context, bridgeID networkid.BridgeID, portalKey networkid.PortalKey, maxTS time.Time) (messageID networkid.MessageID, err error) {
	err = c.QueryRow(ctx, getLastTelegramMessageIDQuery, bridgeID, portalKey.ID, portalKey.Receiver, maxTS.UnixNano()).Scan(&messageID)
	if errors.Is(err, sql.ErrNoRows) {
		err = nil
	}
	return
}

func (c *Container) NamespaceMigratedMessages(ctx context.Context, bridgeID networkid.BridgeID, portalKey networkid.PortalKey) (int64, error) {
	res, err := c.Exec(
		ctx,
		upgrades.NamespaceMigratedMessagesQuery+` AND bridge_id=$2 AND room_id=$3 AND room_receiver=$4`,
		time.Now().UnixMilli(), bridgeID, portalKey.ID, portalKey.Receiver,
	)
	if err != nil {
		return 0, err
	}
	return res.RowsAffected()
}
