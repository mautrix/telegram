package media

import (
	"bytes"
	"context"
	"fmt"
	"net/http"

	"github.com/gotd/td/telegram/downloader"
	"github.com/gotd/td/tg"
)

func getLargestPhotoSize(sizes []tg.PhotoSizeClass) (largest tg.PhotoSizeClass) {
	var maxSize int
	for _, s := range sizes {
		var currentSize int
		switch size := s.(type) {
		case *tg.PhotoSize:
			currentSize = size.GetSize()
		case *tg.PhotoCachedSize:
			currentSize = max(size.GetW(), size.GetH())
		case *tg.PhotoSizeProgressive:
			currentSize = max(size.GetW(), size.GetH())
		case *tg.PhotoPathSize:
			currentSize = len(size.GetBytes())
		case *tg.PhotoStrippedSize:
			currentSize = len(size.GetBytes())
		}

		if currentSize > maxSize {
			maxSize = currentSize
			largest = s
		}
	}
	return
}

func DownloadPhoto(ctx context.Context, client downloader.Client, media *tg.MessageMediaPhoto) (data []byte, mimeType string, err error) {
	p, ok := media.GetPhoto()
	if !ok {
		return nil, "", fmt.Errorf("photo message sent without a photo")
	}
	photo, ok := p.(*tg.Photo)
	if !ok {
		return nil, "", fmt.Errorf("unrecognized photo type %T", p)
	}

	largest := getLargestPhotoSize(photo.GetSizes())
	file := tg.InputPhotoFileLocation{
		ID:            photo.GetID(),
		AccessHash:    photo.GetAccessHash(),
		FileReference: photo.GetFileReference(),
		ThumbSize:     largest.GetType(),
	}

	// TODO convert to streaming?
	var buf bytes.Buffer
	storageFileTypeClass, err := downloader.NewDownloader().Download(client, &file).Stream(ctx, &buf)
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
