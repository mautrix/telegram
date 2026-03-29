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
	"time"

	"go.mau.fi/util/dbutil"
	"maunium.net/go/mautrix/id"
)

const (
	insertTelegramFileQuery          = "INSERT INTO telegram_file (id, mxc, mime_type, size, width, height, timestamp) VALUES ($1, $2, $3, $4, $5, $6, $7)"
	getTelegramFileSelect            = "SELECT id, mxc, mime_type, size, width, height, timestamp FROM telegram_file"
	getTelegramFileByLocationIDQuery = getTelegramFileSelect + " WHERE id=$1"
	getTelegramFileByMXCQuery        = getTelegramFileSelect + " WHERE mxc=$1"
)

type TelegramFileQuery struct {
	*dbutil.QueryHelper[*TelegramFile]
}

type TelegramFileLocationID string

type TelegramFile struct {
	qh *dbutil.QueryHelper[*TelegramFile]

	LocationID TelegramFileLocationID
	MXC        id.ContentURIString
	MIMEType   string
	Size       int
	Width      int
	Height     int
	Timestamp  time.Time
}

var _ dbutil.DataStruct[*TelegramFile] = (*TelegramFile)(nil)

func newTelegramFile(qh *dbutil.QueryHelper[*TelegramFile]) *TelegramFile {
	return &TelegramFile{qh: qh}
}

func (fq *TelegramFileQuery) GetByLocationID(ctx context.Context, locationID TelegramFileLocationID) (*TelegramFile, error) {
	return fq.QueryOne(ctx, getTelegramFileByLocationIDQuery, locationID)
}

func (fq *TelegramFileQuery) GetByMXC(ctx context.Context, mxc id.ContentURIString) (*TelegramFile, error) {
	return fq.QueryOne(ctx, getTelegramFileByMXCQuery, mxc)
}

func (f *TelegramFile) sqlVariables() []any {
	return []any{
		f.LocationID,
		f.MXC,
		dbutil.StrPtr(f.MIMEType),
		dbutil.NumPtr(f.Size),
		dbutil.NumPtr(f.Width),
		dbutil.NumPtr(f.Height),
		dbutil.ConvertedPtr(f.Timestamp, time.Time.Unix),
	}
}

func (f *TelegramFile) Insert(ctx context.Context) error {
	return f.qh.Exec(ctx, insertTelegramFileQuery, f.sqlVariables()...)
}

func (f *TelegramFile) Scan(row dbutil.Scannable) (*TelegramFile, error) {
	var mime sql.NullString
	var size, width, height, timestamp sql.NullInt64
	err := row.Scan(&f.LocationID, &f.MXC, &mime, &size, &width, &height, &timestamp)
	if err != nil {
		return nil, err
	}
	f.MIMEType = mime.String
	f.Size = int(size.Int64)
	f.Width = int(width.Int64)
	f.Height = int(height.Int64)
	if timestamp.Int64 > 0 {
		f.Timestamp = time.Unix(timestamp.Int64, 0)
	}
	return f, nil
}
