package uploader

import (
	"bytes"
	"context"
	"io"
	"io/fs"
	"net/url"
	"os"
	"path/filepath"

	"github.com/go-faster/errors"
	"go.uber.org/multierr"

	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/uploader/source"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

// File is file abstraction.
type File interface {
	Stat() (os.FileInfo, error)
	io.Reader
}

// FromFile uploads given File.
// NB: FromFile does not close given file.
func (u *Uploader) FromFile(ctx context.Context, f File, name string) (tg.InputFileClass, error) {
	info, err := f.Stat()
	if err != nil {
		return nil, errors.Wrap(err, "stat")
	}

	if name == "" {
		name = info.Name()
	}

	return u.Upload(ctx, NewUpload(name, f, info.Size()))
}

// FromPath uploads file from given path.
func (u *Uploader) FromPath(ctx context.Context, path, name string) (tg.InputFileClass, error) {
	return u.FromFS(ctx, osFS{}, path, name)
}

type osFS struct{}

func (o osFS) Open(name string) (fs.File, error) {
	return os.Open(filepath.Clean(name))
}

// FromFS uploads file from fs using given path.
func (u *Uploader) FromFS(ctx context.Context, filesystem fs.FS, path, name string) (_ tg.InputFileClass, err error) {
	f, err := filesystem.Open(path)
	if err != nil {
		return nil, errors.Wrap(err, "open")
	}
	defer func() {
		multierr.AppendInto(&err, f.Close())
	}()

	return u.FromFile(ctx, f, name)
}

// FromReader uploads file from given io.Reader.
// NB: totally stream should not exceed the limit for
// small files (10 MB as docs says, may be a bit bigger).
// Support For Big Files
// https://core.telegram.org/api/files#streamed-uploads
func (u *Uploader) FromReader(ctx context.Context, name string, f io.Reader) (tg.InputFileClass, error) {
	return u.Upload(ctx, NewUpload(name, f, -1))
}

// FromBytes uploads file from given byte slice.
func (u *Uploader) FromBytes(ctx context.Context, name string, b []byte) (tg.InputFileClass, error) {
	return u.Upload(ctx, NewUpload(name, bytes.NewReader(b), int64(len(b))))
}

// FromURL uses given source to upload to Telegram.
func (u *Uploader) FromURL(ctx context.Context, rawURL string) (_ tg.InputFileClass, rerr error) {
	return u.FromSource(ctx, u.src, rawURL)
}

// FromSource uses given source and URL to fetch data and upload it to Telegram.
func (u *Uploader) FromSource(ctx context.Context, src source.Source, rawURL string) (_ tg.InputFileClass, rerr error) {
	parsed, err := url.Parse(rawURL)
	if err != nil {
		return nil, errors.Wrapf(err, "parse url %q", rawURL)
	}

	f, err := src.Open(ctx, parsed)
	if err != nil {
		return nil, errors.Wrapf(err, "open %q", rawURL)
	}
	defer func() {
		multierr.AppendInto(&rerr, f.Close())
	}()

	name := f.Name()
	if name == "" {
		return nil, errors.Errorf("invalid name %q got from %q", name, rawURL)
	}

	size := f.Size()
	if size < 0 {
		size = -1
	}

	return u.Upload(ctx, NewUpload(f.Name(), f, size))
}
