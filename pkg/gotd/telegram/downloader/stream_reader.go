package downloader

import (
	"context"
	"io"

	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

type streamReader struct {
	ctx      context.Context
	reader   *reader
	curBlock block
	last     bool
}

var _ io.Reader = (*streamReader)(nil)

func (s *streamReader) Read(p []byte) (n int, err error) {
	select {
	case <-s.ctx.Done():
		return 0, s.ctx.Err()
	default:
	}

	if len(s.curBlock.data) == 0 {
		if s.last {
			return 0, io.EOF
		} else {
			s.curBlock, err = s.reader.Next(s.ctx)
			if err != nil {
				return 0, err
			}
			s.last = s.curBlock.last()
		}
	}

	n = copy(p, s.curBlock.data)
	s.curBlock.data = s.curBlock.data[n:]
	return
}

func (d *Downloader) streamToReader(ctx context.Context, r *reader) (tg.StorageFileTypeClass, io.Reader, error) {
	first, err := r.Next(ctx)
	if err != nil {
		return nil, nil, err
	}
	return first.tag, &streamReader{ctx, r, first, first.last()}, nil
}
