// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2025 Sumner Evans
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
	"fmt"

	"go.mau.fi/util/dbutil"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/updates"
)

// ScopedStore is a wrapper around a database that implements
// [session.Storage] scoped to a specific Telegram user ID.
type ScopedStore struct {
	db             *dbutil.Database
	telegramUserID int64
}

const (
	// State Storage Queries
	allChannelsQuery   = "SELECT channel_id, pts FROM telegram_channel_state WHERE user_id=$1"
	getChannelPtsQuery = "SELECT pts FROM telegram_channel_state WHERE user_id=$1 AND channel_id=$2"
	setChannelPtsQuery = `
		INSERT INTO telegram_channel_state (user_id, channel_id, pts)
		VALUES ($1, $2, $3)
		ON CONFLICT (user_id, channel_id) DO UPDATE SET pts=excluded.pts
	`
	getStateQuery = "SELECT pts, qts, date, seq from telegram_user_state WHERE user_id=$1"
	setStateQuery = `
		INSERT INTO telegram_user_state (user_id, pts, qts, date, seq)
		VALUES ($1, $2, $3, $4, $5)
		ON CONFLICT (user_id) DO UPDATE SET
			pts=excluded.pts,
			qts=excluded.qts,
			date=excluded.date,
			seq=excluded.seq
	`
	setPtsQuery     = "UPDATE telegram_user_state SET pts=$2 WHERE user_id=$1"
	setQtsQuery     = "UPDATE telegram_user_state SET qts=$2 WHERE user_id=$1"
	setDateQuery    = "UPDATE telegram_user_state SET date=$2 WHERE user_id=$1"
	setSeqQuery     = "UPDATE telegram_user_state SET seq=$2 WHERE user_id=$1"
	setDateSeqQuery = "UPDATE telegram_user_state SET date=$2, seq=$3 WHERE user_id=$1"

	deleteChannelStateForUserQuery = "DELETE FROM telegram_channel_state WHERE user_id=$1"
	deleteUserStateForUserQuery    = "DELETE FROM telegram_user_state WHERE user_id=$1"

	getAccessHashQuery = "SELECT access_hash FROM telegram_access_hash WHERE user_id=$1 AND entity_type=$2 AND entity_id=$3"
	setAccessHashQuery = `
		INSERT INTO telegram_access_hash (user_id, entity_type, entity_id, access_hash)
		VALUES ($1, $2, $3, $4)
		ON CONFLICT (user_id, entity_type, entity_id) DO UPDATE SET access_hash=excluded.access_hash
	`
	deleteAccessHashesForUserQuery = "DELETE FROM telegram_access_hash WHERE user_id=$1"
)

var _ updates.StateStorage = (*ScopedStore)(nil)

type channelIDPtsTuple struct {
	ChannelID int64
	Pts       int
}

var ciptScanner = dbutil.ConvertRowFn[channelIDPtsTuple](func(row dbutil.Scannable) (cipt channelIDPtsTuple, err error) {
	err = row.Scan(&cipt.ChannelID, &cipt.Pts)
	return
})

func (s *ScopedStore) ForEachChannels(ctx context.Context, userID int64, f func(ctx context.Context, channelID int64, pts int) error) error {
	s.assertUserIDMatches(userID)
	items, err := ciptScanner.NewRowIter(s.db.Query(ctx, allChannelsQuery, userID)).AsList()
	if err != nil {
		return err
	}
	for _, item := range items {
		err = f(ctx, item.ChannelID, item.Pts)
		if err != nil {
			return fmt.Errorf("iteration error for channel %d: %w", item.ChannelID, err)
		}
	}
	return nil
}

func (s *ScopedStore) GetChannelPts(ctx context.Context, userID int64, channelID int64) (pts int, found bool, err error) {
	s.assertUserIDMatches(userID)
	err = s.db.QueryRow(ctx, getChannelPtsQuery, userID, channelID).Scan(&pts)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, false, nil
	}
	return pts, err == nil, err
}

func (s *ScopedStore) SetChannelPts(ctx context.Context, userID int64, channelID int64, pts int) (err error) {
	s.assertUserIDMatches(userID)
	_, err = s.db.Exec(ctx, setChannelPtsQuery, userID, channelID, pts)
	return
}

func (s *ScopedStore) GetState(ctx context.Context, userID int64) (state updates.State, found bool, err error) {
	s.assertUserIDMatches(userID)
	err = s.db.QueryRow(ctx, getStateQuery, userID).Scan(&state.Pts, &state.Qts, &state.Date, &state.Seq)
	if errors.Is(err, sql.ErrNoRows) {
		return state, false, nil
	}
	return state, err == nil, err
}

func (s *ScopedStore) SetState(ctx context.Context, userID int64, state updates.State) (err error) {
	s.assertUserIDMatches(userID)
	_, err = s.db.Exec(ctx, setStateQuery, userID, state.Pts, state.Qts, state.Date, state.Seq)
	return
}

func (s *ScopedStore) SetPts(ctx context.Context, userID int64, pts int) (err error) {
	s.assertUserIDMatches(userID)
	_, err = s.db.Exec(ctx, setPtsQuery, userID, pts)
	return
}

func (s *ScopedStore) SetQts(ctx context.Context, userID int64, qts int) (err error) {
	s.assertUserIDMatches(userID)
	_, err = s.db.Exec(ctx, setQtsQuery, userID, qts)
	return
}

func (s *ScopedStore) SetSeq(ctx context.Context, userID int64, seq int) (err error) {
	s.assertUserIDMatches(userID)
	_, err = s.db.Exec(ctx, setSeqQuery, userID, seq)
	return
}

func (s *ScopedStore) SetDate(ctx context.Context, userID int64, date int) (err error) {
	s.assertUserIDMatches(userID)
	_, err = s.db.Exec(ctx, setDateQuery, userID, date)
	return
}

func (s *ScopedStore) SetDateSeq(ctx context.Context, userID int64, date int, seq int) (err error) {
	s.assertUserIDMatches(userID)
	_, err = s.db.Exec(ctx, setDateSeqQuery, userID, date, seq)
	return
}

func (s *ScopedStore) DeleteUserState(ctx context.Context) (err error) {
	_, err = s.db.Exec(ctx, deleteUserStateForUserQuery, s.telegramUserID)
	return
}

func (s *ScopedStore) DeleteChannelStateForUser(ctx context.Context) (err error) {
	_, err = s.db.Exec(ctx, deleteChannelStateForUserQuery, s.telegramUserID)
	return
}

var _ updates.AccessHasher = (*ScopedStore)(nil)

// Deprecated: only for interface, don't use directly. Use [GetAccessHash]
// instead.
func (s *ScopedStore) GetChannelAccessHash(ctx context.Context, forUserID, channelID int64) (int64, bool, error) {
	s.assertUserIDMatches(forUserID)
	accessHash, err := s.GetAccessHash(ctx, ids.PeerTypeChannel, channelID)
	if errors.Is(err, ErrNoAccessHash) {
		return 0, false, nil
	}
	return accessHash, true, err
}

// Deprecated: only for interface, don't use directly. Use [SetAccessHash]
// instead.
func (s *ScopedStore) SetChannelAccessHash(ctx context.Context, forUserID, channelID, accessHash int64) (err error) {
	s.assertUserIDMatches(forUserID)
	return s.SetAccessHash(ctx, ids.PeerTypeChannel, channelID, accessHash)
}

// Deprecated: only for interface, don't use directly. Use [GetAccessHash]
// instead.
func (s *ScopedStore) GetUserAccessHash(ctx context.Context, forUserID int64, userID int64) (int64, bool, error) {
	s.assertUserIDMatches(forUserID)
	accessHash, err := s.GetAccessHash(ctx, ids.PeerTypeUser, userID)
	if errors.Is(err, ErrNoAccessHash) {
		return 0, false, nil
	}
	return accessHash, true, err
}

// Deprecated: only for interface, don't use directly. Use [SetAccessHash]
// instead.
func (s *ScopedStore) SetUserAccessHash(ctx context.Context, forUserID int64, userID int64, accessHash int64) error {
	s.assertUserIDMatches(forUserID)
	return s.SetAccessHash(ctx, ids.PeerTypeUser, userID, accessHash)
}

var ErrNoAccessHash = errors.New("access hash not found")

func (s *ScopedStore) GetAccessHash(ctx context.Context, entityType ids.PeerType, entityID int64) (accessHash int64, err error) {
	err = s.db.QueryRow(ctx, getAccessHashQuery, s.telegramUserID, entityType, entityID).Scan(&accessHash)
	if errors.Is(err, sql.ErrNoRows) {
		err = fmt.Errorf("%w for %d", ErrNoAccessHash, entityID)
	}
	return
}

func (s *ScopedStore) SetAccessHash(ctx context.Context, entityType ids.PeerType, entityID, accessHash int64) (err error) {
	_, err = s.db.Exec(ctx, setAccessHashQuery, s.telegramUserID, entityType, entityID, accessHash)
	return
}

func (s *ScopedStore) DeleteAccessHashesForUser(ctx context.Context) (err error) {
	_, err = s.db.Exec(ctx, deleteAccessHashesForUserQuery, s.telegramUserID)
	return
}

// Helper Functions

func (s *ScopedStore) assertUserIDMatches(userID int64) {
	if s.telegramUserID != userID {
		panic(fmt.Sprintf("scoped store for %d function called with user ID %d", s.telegramUserID, userID))
	}
}
