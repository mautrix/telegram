package download

import (
	"bytes"
	"context"
	"fmt"
	"net/http"

	"github.com/gotd/td/telegram/downloader"
	"github.com/gotd/td/tg"
)

type dimensionable interface {
	GetW() int
	GetH() int
}

func GetLargestPhotoSize(sizes []tg.PhotoSizeClass) (width, height int, largest tg.PhotoSizeClass) {
	if len(sizes) == 0 {
		panic("cannot get largest size from empty list of sizes")
	}

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
			if d, ok := s.(dimensionable); ok {
				width = d.GetW()
				height = d.GetH()
			}
		}
	}
	return
}

func GetLargestDimensions(sizes []tg.PhotoSizeClass) (width, height int) {
	for _, s := range sizes {
		switch size := s.(type) {
		case *tg.PhotoCachedSize:
			width = size.GetW()
			height = size.GetH()
		case *tg.PhotoSizeProgressive:
			width = size.GetW()
			height = size.GetH()
		}
	}
	return
}

func DownloadPhotoFileLocation(ctx context.Context, client downloader.Client, file tg.InputFileLocationClass) (data []byte, mimeType string, err error) {
	// TODO convert to streaming?
	var buf bytes.Buffer
	storageFileTypeClass, err := downloader.NewDownloader().Download(client, file).Stream(ctx, &buf)
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

func DownloadPhoto(ctx context.Context, client downloader.Client, photo *tg.Photo) (data []byte, width, height int, mimeType string, err error) {
	var largest tg.PhotoSizeClass
	width, height, largest = GetLargestPhotoSize(photo.GetSizes())
	data, mimeType, err = DownloadPhotoFileLocation(ctx, client, &tg.InputPhotoFileLocation{
		ID:            photo.GetID(),
		AccessHash:    photo.GetAccessHash(),
		FileReference: photo.GetFileReference(),
		ThumbSize:     largest.GetType(),
	})
	return
}

func DownloadPhotoMedia(ctx context.Context, client downloader.Client, media *tg.MessageMediaPhoto) (data []byte, width, height int, mimeType string, err error) {
	p, ok := media.GetPhoto()
	if !ok {
		return nil, 0, 0, "", fmt.Errorf("photo message sent without a photo")
	}
	photo, ok := p.(*tg.Photo)
	if !ok {
		return nil, 0, 0, "", fmt.Errorf("unrecognized photo type %T", p)
	}
	return DownloadPhoto(ctx, client, photo)
}
