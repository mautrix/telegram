package downloader

import (
	"context"
	"crypto/aes"
	"crypto/cipher"
	"encoding/binary"

	"github.com/go-faster/errors"

	"go.mau.fi/mautrix-telegram/pkg/gotd/bin"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

// ExpiredTokenError error is returned when Downloader get expired file token for CDN.
// See https://core.telegram.org/constructor/upload.fileCdnRedirect.
type ExpiredTokenError struct {
	*tg.UploadCDNFileReuploadNeeded
}

// Error implements error interface.
func (r *ExpiredTokenError) Error() string {
	return "redirect to master DC for requesting new file token"
}

// cdn is a CDN DC download schema.
// See https://core.telegram.org/cdn#getting-files-from-a-cdn.
type cdn struct {
	cdn      CDN
	client   Client
	pool     *bin.Pool
	redirect *tg.UploadFileCDNRedirect
}

var _ schema = cdn{}

// decrypt decrypts file chunk from Telegram CDN.
// See https://core.telegram.org/cdn#decrypting-files.
func (c cdn) decrypt(src []byte, offset int64) ([]byte, error) {
	block, err := aes.NewCipher(c.redirect.EncryptionKey)
	if err != nil {
		return nil, errors.Wrap(err, "create cipher")
	}

	if block.BlockSize() != len(c.redirect.EncryptionIv) {
		return nil, errors.Errorf(
			"invalid IV or key length, block size %d != IV %d",
			block.BlockSize(), len(c.redirect.EncryptionIv),
		)
	}

	// Copy IV to buffer from Pool.
	iv := c.pool.GetSize(len(c.redirect.EncryptionIv))
	defer c.pool.Put(iv)
	copy(iv.Buf, c.redirect.EncryptionIv)

	// For IV, it should use the value of encryption_iv, modified in the following manner:
	// for each offset replace the last 4 bytes of the encryption_iv with offset / 16 in big-endian.
	binary.BigEndian.PutUint32(iv.Buf[iv.Len()-4:], uint32(offset/16))

	dst := make([]byte, len(src))
	cipher.NewCTR(block, iv.Buf).XORKeyStream(dst, src)
	return dst, nil
}

func (c cdn) Chunk(ctx context.Context, offset int64, limit int) (chunk, error) {
	r, err := c.cdn.UploadGetCDNFile(ctx, &tg.UploadGetCDNFileRequest{
		Offset:    offset,
		Limit:     limit,
		FileToken: c.redirect.FileToken,
	})
	if err != nil {
		return chunk{}, err
	}

	switch result := r.(type) {
	case *tg.UploadCDNFile:
		data, err := c.decrypt(result.Bytes, offset)
		if err != nil {
			return chunk{}, err
		}

		return chunk{
			data: data,
		}, nil
	case *tg.UploadCDNFileReuploadNeeded:
		return chunk{}, &ExpiredTokenError{UploadCDNFileReuploadNeeded: result}
	default:
		return chunk{}, errors.Errorf("unexpected type %T", r)
	}
}

func (c cdn) Hashes(ctx context.Context, offset int64) ([]tg.FileHash, error) {
	return c.client.UploadGetCDNFileHashes(ctx, &tg.UploadGetCDNFileHashesRequest{
		FileToken: c.redirect.FileToken,
		Offset:    offset,
	})
}
