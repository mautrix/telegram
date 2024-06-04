package store

import (
	"context"

	"github.com/gotd/td/session"
	"go.mau.fi/util/dbutil"
)

// SessionStore is a wrapper around a database that implements
// [session.Storage] scoped to a specific Telegram user ID.
type SessionStore struct {
	db             *dbutil.Database
	telegramUserID int64
}

var _ session.Storage = (*SessionStore)(nil)

const (
	loadSessionQuery  = `SELECT session_data FROM telegram_session WHERE user_id=$1`
	storeSessionQuery = `
		INSERT INTO telegram_session (user_id, session_data)
		VALUES ($1, $2)
		ON CONFLICT (user_id) DO UPDATE SET session_data=excluded.session_data
	`
)

// LoadSession loads session data from the database.
func (s *SessionStore) LoadSession(ctx context.Context) (sessionData []byte, err error) {
	row := s.db.QueryRow(ctx, loadSessionQuery, s.telegramUserID)
	err = row.Scan(&sessionData)
	return
}

// StoreSession stores session data for a login into the database.
func (s *SessionStore) StoreSession(ctx context.Context, data []byte) error {
	_, err := s.db.Exec(ctx, storeSessionQuery, s.telegramUserID, data)
	return err
}
