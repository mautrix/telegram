package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"time"

	"go.mau.fi/util/dbutil"
)

const (
	insertTelegramFileQuery = `
		INSERT INTO telegram_file (
			id, mxc, mime_type, was_converted, timestamp, size, width, height, thumbnail, decryption_info)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
	`
	getTelegramFileSelect = `
		SELECT id, mxc, mime_type, was_converted, timestamp, size, width, height, thumbnail, decryption_info
		FROM telegram_file
	`
	getTelegramFileByLocationIDQuery = getTelegramFileSelect + "WHERE id=$1"
	getTelegramFileByMXCQuery        = getTelegramFileSelect + "WHERE mxc=$1"
)

type TelegramFileQuery struct {
	*dbutil.QueryHelper[*TelegramFile]
}

type TelegramFileLocationID string

type TelegramFile struct {
	qh *dbutil.QueryHelper[*TelegramFile]

	LocationID     TelegramFileLocationID
	MXC            string
	MimeType       string
	WasConverted   bool
	Timestamp      time.Time
	Size           int64
	Width          int
	Height         int
	ThumbnailID    string
	DecryptionInfo json.RawMessage
}

var _ dbutil.DataStruct[*TelegramFile] = (*TelegramFile)(nil)

func newTelegramFile(qh *dbutil.QueryHelper[*TelegramFile]) *TelegramFile {
	return &TelegramFile{qh: qh}
}

func (fq *TelegramFileQuery) GetByLocationID(ctx context.Context, locationID string) (*TelegramFile, error) {
	return fq.QueryOne(ctx, getTelegramFileByLocationIDQuery, locationID)
}

func (fq *TelegramFileQuery) GetByMXC(ctx context.Context, mxc string) (*TelegramFile, error) {
	return fq.QueryOne(ctx, getTelegramFileByMXCQuery, mxc)
}

func (f *TelegramFile) sqlVariables() []any {
	return []any{
		f.LocationID,
		f.MXC,
		f.MimeType,
		f.WasConverted,
		f.Timestamp.UnixMilli(),
		f.Size,
		f.Width,
		f.Height,
		f.ThumbnailID,
		f.DecryptionInfo,
	}
}

func (f *TelegramFile) Insert(ctx context.Context) error {
	return f.qh.Exec(ctx, insertTelegramFileQuery, f.sqlVariables()...)
}

func (f *TelegramFile) Scan(row dbutil.Scannable) (*TelegramFile, error) {
	var thumbnailID sql.NullString
	var timestamp int64
	err := row.Scan(
		&f.LocationID,
		&f.MXC,
		&f.MimeType,
		&f.WasConverted,
		&timestamp,
		&f.Size,
		&f.Width,
		&f.Height,
		&thumbnailID,
		&f.DecryptionInfo,
	)
	f.Timestamp = time.UnixMilli(timestamp)
	f.ThumbnailID = thumbnailID.String
	return f, err
}
