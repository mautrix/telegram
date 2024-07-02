package media

import (
	"context"
	"fmt"

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

func DownloadPhoto(ctx context.Context, client downloader.Client, photo *tg.Photo) (data []byte, width, height int, mimeType string, err error) {
	var largest tg.PhotoSizeClass
	width, height, largest = GetLargestPhotoSize(photo.GetSizes())
	data, mimeType, err = DownloadFileLocation(ctx, client, &tg.InputPhotoFileLocation{
		ID:            photo.GetID(),
		AccessHash:    photo.GetAccessHash(),
		FileReference: photo.GetFileReference(),
		ThumbSize:     largest.GetType(),
	})
	return
}

func DownloadPhotoMedia(ctx context.Context, client downloader.Client, media *tg.MessageMediaPhoto) (data []byte, width, height int, mimeType string, err error) {
	if p, ok := media.GetPhoto(); !ok {
		return nil, 0, 0, "", fmt.Errorf("photo message sent without a photo")
	} else if photo, ok := p.(*tg.Photo); !ok {
		return nil, 0, 0, "", fmt.Errorf("unrecognized photo type %T", p)
	} else {
		return DownloadPhoto(ctx, client, photo)
	}
}
