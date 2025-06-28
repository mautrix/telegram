package downloader

import (
	"context"

	"github.com/go-faster/errors"

	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

var errHashesNotSupported = errors.New("this schema does not support hashes fetch")

// web is a web file download schema.
// See https://core.telegram.org/api/files#downloading-webfiles.
type web struct {
	client Client

	location tg.InputWebFileLocationClass
}

var _ schema = web{}

func (w web) Chunk(ctx context.Context, offset int64, limit int) (chunk, error) {
	file, err := w.client.UploadGetWebFile(ctx, &tg.UploadGetWebFileRequest{
		Location: w.location,
		Offset:   int(offset),
		Limit:    limit,
	})
	if err != nil {
		return chunk{}, err
	}

	return chunk{data: file.Bytes, tag: file.FileType}, nil
}

func (w web) Hashes(ctx context.Context, offset int64) ([]tg.FileHash, error) {
	return nil, errHashesNotSupported
}
