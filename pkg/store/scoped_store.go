package store

import (
	"context"
	"database/sql"
	"errors"
	"fmt"

	"github.com/gotd/td/session"
	"github.com/gotd/td/telegram/updates"
	"go.mau.fi/util/dbutil"
)

// scopedStore is a wrapper around a database that implements
// [session.Storage] scoped to a specific Telegram user ID.
type scopedStore struct {
	db             *dbutil.Database
	telegramUserID int64
}

const (
	// Session Storage Queries
	loadSessionQuery  = `SELECT session_data FROM telegram_session WHERE user_id=$1`
	storeSessionQuery = `
		INSERT INTO telegram_session (user_id, session_data)
		VALUES ($1, $2)
		ON CONFLICT (user_id) DO UPDATE SET session_data=excluded.session_data
	`

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
	setPtsQuery     = "UPDATE telegram_user_state SET pts=$1 WHERE user_id=$2"
	setQtsQuery     = "UPDATE telegram_user_state SET qts=$1 WHERE user_id=$2"
	setDateQuery    = "UPDATE telegram_user_state SET date=$1 WHERE user_id=$2"
	setSeqQuery     = "UPDATE telegram_user_state SET seq=$1 WHERE user_id=$2"
	setDateSeqQuery = "UPDATE telegram_user_state SET date=$1, seq=$2 WHERE user_id=$3"

	// Channel Access Hasher Queries
	getChannelAccessHashQuery = "SELECT access_hash FROM telegram_channel_access_hashes WHERE user_id=$1 AND channel_id=$2"
	setChannelAccessHashQuery = `
		INSERT INTO telegram_channel_access_hashes (user_id, channel_id, access_hash)
		VALUES ($1, $2, $3)
		ON CONFLICT (user_id, channel_id) DO UPDATE SET access_hash=excluded.access_hash
	`
)

var _ session.Storage = (*scopedStore)(nil)

func (s *scopedStore) LoadSession(ctx context.Context) (sessionData []byte, err error) {
	row := s.db.QueryRow(ctx, loadSessionQuery, s.telegramUserID)
	err = row.Scan(&sessionData)
	return
}

func (s *scopedStore) StoreSession(ctx context.Context, data []byte) error {
	_, err := s.db.Exec(ctx, storeSessionQuery, s.telegramUserID, data)
	return err
}

var _ updates.StateStorage = (*scopedStore)(nil)

func (s *scopedStore) ForEachChannels(ctx context.Context, userID int64, f func(ctx context.Context, channelID int64, pts int) error) error {
	s.assertUserIDMatches(userID)
	rows, err := s.db.Query(ctx, allChannelsQuery, userID)
	if err != nil {
		return err
	}
	var channelID int64
	var pts int
	for rows.Next() {
		if err = rows.Scan(&channelID, &pts); err != nil {
			return err
		} else if err = f(ctx, channelID, pts); err != nil {
			return err
		}
	}
	return nil
}

func (s *scopedStore) GetChannelPts(ctx context.Context, userID int64, channelID int64) (pts int, found bool, err error) {
	s.assertUserIDMatches(userID)
	err = s.db.QueryRow(ctx, getChannelPtsQuery, userID, channelID).Scan(&pts)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, false, nil
	}
	return pts, err == nil, err
}

func (s *scopedStore) SetChannelPts(ctx context.Context, userID int64, channelID int64, pts int) (err error) {
	s.assertUserIDMatches(userID)
	_, err = s.db.Exec(ctx, setChannelPtsQuery, userID, channelID, pts)
	return
}

func (s *scopedStore) GetState(ctx context.Context, userID int64) (state updates.State, found bool, err error) {
	s.assertUserIDMatches(userID)
	err = s.db.QueryRow(ctx, getStateQuery, userID).Scan(&state.Pts, &state.Qts, &state.Date, &state.Seq)
	if errors.Is(err, sql.ErrNoRows) {
		return state, false, nil
	}
	return state, err == nil, err
}

func (s *scopedStore) SetState(ctx context.Context, userID int64, state updates.State) (err error) {
	s.assertUserIDMatches(userID)
	_, err = s.db.Exec(ctx, setStateQuery, userID, state.Pts, state.Qts, state.Date, state.Seq)
	return
}

func (s *scopedStore) SetPts(ctx context.Context, userID int64, pts int) (err error) {
	s.assertUserIDMatches(userID)
	_, err = s.db.Exec(ctx, setPtsQuery, userID, pts)
	return
}

func (s *scopedStore) SetQts(ctx context.Context, userID int64, qts int) (err error) {
	s.assertUserIDMatches(userID)
	_, err = s.db.Exec(ctx, setQtsQuery, userID, qts)
	return
}

func (s *scopedStore) SetSeq(ctx context.Context, userID int64, seq int) (err error) {
	s.assertUserIDMatches(userID)
	_, err = s.db.Exec(ctx, setSeqQuery, userID, seq)
	return
}

func (s *scopedStore) SetDate(ctx context.Context, userID int64, date int) (err error) {
	s.assertUserIDMatches(userID)
	_, err = s.db.Exec(ctx, setDateQuery, userID, date)
	return
}

func (s *scopedStore) SetDateSeq(ctx context.Context, userID int64, date int, seq int) (err error) {
	s.assertUserIDMatches(userID)
	_, err = s.db.Exec(ctx, setDateSeqQuery, userID, date, seq)
	return
}

var _ updates.ChannelAccessHasher = (*scopedStore)(nil)

func (s *scopedStore) GetChannelAccessHash(ctx context.Context, userID int64, channelID int64) (accessHash int64, found bool, err error) {
	s.assertUserIDMatches(userID)
	err = s.db.QueryRow(ctx, getChannelAccessHashQuery, userID, channelID).Scan(&accessHash)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, false, nil
	}
	return accessHash, err == nil, err
}

func (s *scopedStore) SetChannelAccessHash(ctx context.Context, userID int64, channelID int64, accessHash int64) (err error) {
	s.assertUserIDMatches(userID)
	_, err = s.db.Exec(ctx, setChannelAccessHashQuery, userID, channelID, accessHash)
	return
}

// Helper Functions

func (s *scopedStore) assertUserIDMatches(userID int64) {
	if s.telegramUserID != userID {
		panic(fmt.Sprintf("scoped store for %d function called with user ID %d", s.telegramUserID, userID))
	}
}
