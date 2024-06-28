package media

import (
	"bytes"
	"context"
	"net/http"

	"github.com/gotd/td/telegram/downloader"
	"github.com/gotd/td/tg"
)

func DownloadFileLocation(ctx context.Context, client downloader.Client, loc tg.InputFileLocationClass) (data []byte, mimeType string, err error) {
	// TODO convert entire function to streaming? Maybe at least stream to file?
	var buf bytes.Buffer
	storageFileTypeClass, err := downloader.NewDownloader().Download(client, loc).Stream(ctx, &buf)
	if err != nil {
		return nil, "", err
	}
	switch storageFileTypeClass.(type) {
	case *tg.StorageFileJpeg:
		mimeType = "image/jpeg"
	case *tg.StorageFileGif:
		mimeType = "image/gif"
	case *tg.StorageFilePng:
		mimeType = "image/png"
	case *tg.StorageFilePdf:
		mimeType = "application/pdf"
	case *tg.StorageFileMp3:
		mimeType = "audio/mp3"
	case *tg.StorageFileMov:
		mimeType = "video/quicktime"
	case *tg.StorageFileMp4:
		mimeType = "video/mp4"
	case *tg.StorageFileWebp:
		mimeType = "image/webp"
	default:
		mimeType = http.DetectContentType(buf.Bytes())
	}
	return buf.Bytes(), mimeType, nil
}
