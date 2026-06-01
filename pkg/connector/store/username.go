// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2025 Tulir Asokan
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
	"strings"

	"go.mau.fi/util/dbutil"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
)

type UsernameQuery struct {
	db *dbutil.Database
}

const (
	getUsernameQuery = "SELECT username FROM telegram_username WHERE entity_type=$1 AND entity_id=$2"
	setUsernameQuery = `
		INSERT INTO telegram_username (username, entity_type, entity_id)
		VALUES ($1, $2, $3)
		ON CONFLICT (username) DO UPDATE SET
			entity_type=excluded.entity_type,
			entity_id=excluded.entity_id
	`
	getByUsernameQuery  = "SELECT entity_type, entity_id FROM telegram_username WHERE LOWER(username)=$1"
	clearUsernameQuery  = `DELETE FROM telegram_username WHERE entity_type=$1 AND entity_id=$2 AND LOWER(username)<>$3`
	deleteUsernameQuery = `DELETE FROM telegram_username WHERE LOWER(username)=$1`
)

func (s *UsernameQuery) Get(ctx context.Context, entityType ids.PeerType, userID int64) (username string, err error) {
	err = s.db.QueryRow(ctx, getUsernameQuery, entityType, userID).Scan(&username)
	if errors.Is(err, sql.ErrNoRows) {
		err = nil
	}
	return
}

func (s *UsernameQuery) Set(ctx context.Context, entityType ids.PeerType, entityID int64, username string) (err error) {
	if username == "" {
		_, err = s.db.Exec(ctx, clearUsernameQuery, entityType, entityID, "")
	} else {
		_, err = s.db.Exec(ctx, setUsernameQuery, username, entityType, entityID)
		if err == nil {
			_, err = s.db.Exec(ctx, clearUsernameQuery, entityType, entityID, strings.ToLower(username))
		}
	}
	return
}

func (s *UsernameQuery) Delete(ctx context.Context, username string) (err error) {
	_, err = s.db.Exec(ctx, deleteUsernameQuery, strings.ToLower(username))
	return
}

func (s *UsernameQuery) GetEntityID(ctx context.Context, username string) (entityType ids.PeerType, entityID int64, err error) {
	err = s.db.QueryRow(ctx, getByUsernameQuery, strings.ToLower(username)).Scan(&entityType, &entityID)
	if errors.Is(err, sql.ErrNoRows) {
		err = nil
	}
	return
}
