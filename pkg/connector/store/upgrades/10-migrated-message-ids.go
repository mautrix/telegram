// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2026 Tulir Asokan
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

package upgrades

import (
	"context"
	"time"

	"github.com/rs/zerolog"
	"go.mau.fi/util/dbutil"
)

const NamespaceMigratedMessagesQuery = `
UPDATE message
SET id='migrated:' || id || ':' || $1
WHERE id NOT LIKE '%.%' AND id NOT LIKE '%:%'
`

var upgradeV10 = dbutil.WrapUpgrade(-1, 10, 2, "Namespace historical minigroup messages in supergroups", dbutil.TxnModeOn, func(ctx context.Context, db *dbutil.Database) error {
	res, err := db.Exec(ctx, NamespaceMigratedMessagesQuery+` AND room_id LIKE 'channel:%'`, time.Now().UnixMilli())
	if err != nil {
		return err
	}
	affected, err := res.RowsAffected()
	if err != nil {
		return err
	}
	if affected > 0 {
		zerolog.Ctx(ctx).Info().Int64("affected", affected).Msg("Updated IDs of historical minigroup messages in supergroups")
	}
	return nil
})
