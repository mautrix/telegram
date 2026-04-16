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

	"go.mau.fi/util/dbutil"
)

func init() {
	Table.Register(-1, 9, 2, "Fix bug in legacy migration", dbutil.TxnModeOn, func(ctx context.Context, db *dbutil.Database) error {
		if db.Dialect != dbutil.SQLite {
			return nil
		}
		exists, err := db.TableExists(ctx, "new_mx_room_state")
		if !exists || err != nil {
			return err
		}
		_, err = db.Exec(ctx, `
			DROP TABLE mx_room_state;
			ALTER TABLE new_mx_room_state RENAME TO mx_room_state;
		`)
		return err
	})
}
