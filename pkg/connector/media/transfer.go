package media

import (
	"bytes"
	"context"
	"fmt"
	"net/http"

	"github.com/gotd/td/telegram/downloader"
	"github.com/gotd/td/tg"
	"github.com/rs/zerolog"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/event"
	"maunium.net/go/mautrix/id"

	"go.mau.fi/util/gnuzip"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/connector/store"
)

type dimensionable interface {
	GetW() int
	GetH() int
}

func getLargestPhotoSize(sizes []tg.PhotoSizeClass) (width, height int, largest tg.PhotoSizeClass) {
	if len(sizes) == 0 {
		panic("cannot get largest size from empty list of sizes")
	}

	// FIXME this max size seems to be confusing bytes and dimensions.
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

// getLocationID converts a Telegram [tg.Document],
// [tg.InputDocumentFileLocation], [tg.InputPeerPhotoFileLocation],
// [tg.InputFileLocation], or [tg.InputPhotoFileLocation] into a [LocationID]
// for use in the telegram_file table.
func getLocationID(loc any) (locID store.TelegramFileLocationID) {
	var id string
	switch location := loc.(type) {
	case *tg.Document:
		id = fmt.Sprintf("%d", location.ID)
	case *tg.InputDocumentFileLocation:
		id = fmt.Sprintf("%d-%s", location.ID, location.ThumbSize)
	case *tg.InputPhotoFileLocation:
		id = fmt.Sprintf("%d-%s", location.ID, location.ThumbSize)
	case *tg.InputFileLocation:
		id = fmt.Sprintf("%d-%d", location.VolumeID, location.LocalID)
	case *tg.InputPeerPhotoFileLocation:
		id = fmt.Sprintf("%d", location.PhotoID)
	default:
		panic(fmt.Errorf("unknown location type %T", location))
	}
	return store.TelegramFileLocationID(id)
}

// Transferer is a utility for downloading media from Telegram and uploading it
// to Matrix.
// TODO better name?
type Transferer struct {
	client downloader.Client

	roomID                id.RoomID
	filename              string
	animatedStickerConfig *AnimatedStickerConfig

	fileInfo event.FileInfo
}

type ReadyTransferer struct {
	inner *Transferer
	loc   tg.InputFileLocationClass
}

// NewTransferer creates a new [Transferer] with the given [downloader.Client].
// The client is used to download the media from Telegram.
func NewTransferer(client downloader.Client) *Transferer {
	return &Transferer{client: client}
}

// WithRoomID sets the room ID for the [Transferer].
func (t *Transferer) WithRoomID(roomID id.RoomID) *Transferer {
	t.roomID = roomID
	return t
}

// WithFilename sets the filename for the [Transferer].
func (t *Transferer) WithFilename(filename string) *Transferer {
	t.filename = filename
	return t
}

// WithStickerConfig sets the animated sticker config for the [Transferer].
func (t *Transferer) WithStickerConfig(cfg AnimatedStickerConfig) *Transferer {
	t.animatedStickerConfig = &cfg
	switch cfg.Target {
	case "png":
		t.fileInfo.MimeType = "image/png"
	case "gif":
		t.fileInfo.MimeType = "image/gif"
	case "webp":
		t.fileInfo.MimeType = "image/webp"
	case "webm":
		t.fileInfo.MimeType = "video/webm"
	}
	return t
}

func (t *Transferer) WithThumbnail(uri id.ContentURIString, file *event.EncryptedFileInfo, info *event.FileInfo) *Transferer {
	t.fileInfo.ThumbnailURL = uri
	t.fileInfo.ThumbnailFile = file
	t.fileInfo.ThumbnailInfo = info
	return t
}

func (t *Transferer) WithVideo(attr *tg.DocumentAttributeVideo) *Transferer {
	t.fileInfo.Width, t.fileInfo.Height = attr.W, attr.H
	t.fileInfo.Duration = int(attr.Duration * 1000)
	return t
}

func (t *Transferer) WithImageSize(attr *tg.DocumentAttributeImageSize) *Transferer {
	t.fileInfo.Width, t.fileInfo.Height = attr.W, attr.H
	return t
}

// WithDocument transforms a [Transferer] to a [ReadyTransferer] by setting the
// given document as the location that will be downloaded by the
// [ReadyTransferer].
func (t *Transferer) WithDocument(doc tg.DocumentClass, thumbnail bool) *ReadyTransferer {
	document := doc.(*tg.Document)
	documentFileLocation := tg.InputDocumentFileLocation{
		ID:            document.GetID(),
		AccessHash:    document.GetAccessHash(),
		FileReference: document.GetFileReference(),
	}
	if thumbnail {
		_, _, largestThumbnail := getLargestPhotoSize(document.Thumbs)
		documentFileLocation.ThumbSize = largestThumbnail.GetType()
	} else {
		t.fileInfo.Size = int(document.Size)
		if t.fileInfo.MimeType == "" {
			t.fileInfo.MimeType = document.GetMimeType()
		}
	}
	return &ReadyTransferer{t, &documentFileLocation}
}

// WithPhoto transforms a [Transferer] to a [ReadyTransferer] by setting the
// given photo as the location that will be downloaded by the
// [ReadyTransferer].
func (t *Transferer) WithPhoto(pc tg.PhotoClass) *ReadyTransferer {
	photo := pc.(*tg.Photo)
	var largest tg.PhotoSizeClass
	t.fileInfo.Width, t.fileInfo.Height, largest = getLargestPhotoSize(photo.GetSizes())
	return &ReadyTransferer{
		inner: t,
		loc: &tg.InputPhotoFileLocation{
			ID:            photo.GetID(),
			AccessHash:    photo.GetAccessHash(),
			FileReference: photo.GetFileReference(),
			ThumbSize:     largest.GetType(),
		},
	}
}

// WithUser transforms a [Transferer] to a [ReadyTransferer] by setting the
// given user's photo as the location that will be downloaded by the
// [ReadyTransferer].
func (t *Transferer) WithUserPhoto(ctx context.Context, store *store.ScopedStore, user *tg.User, photoID int64) (*ReadyTransferer, error) {
	if accessHash, err := store.GetAccessHash(ctx, user.GetID()); err != nil {
		return nil, fmt.Errorf("failed to get user access hash for %d: %w", user.GetID(), err)
	} else {
		return &ReadyTransferer{
			inner: t,
			loc: &tg.InputPeerPhotoFileLocation{
				Peer:    &tg.InputPeerUser{UserID: user.GetID(), AccessHash: accessHash},
				PhotoID: photoID,
				Big:     true,
			},
		}, nil
	}
}

// Transfer downloads the media from Telegram and uploads it to Matrix.
//
// If the file is already in the database, the MXC URI will be reused. The
// file's MXC URI will only be cached if the room ID is unset or if the room is
// not encrypted.
//
// If there is a sticker config on the [Transferer], this function converts
// animated stickers to the target format specified by the specified
// [AnimatedStickerConfig].
func (t *ReadyTransferer) Transfer(ctx context.Context, store *store.Container, intent bridgev2.MatrixAPI) (mxc id.ContentURIString, encryptedFileInfo *event.EncryptedFileInfo, outFileInfo *event.FileInfo, err error) {
	locationID := getLocationID(t.loc)
	log := zerolog.Ctx(ctx).With().
		Str("component", "media_transfer").
		Str("location_id", string(locationID)).
		Logger()
	ctx = log.WithContext(ctx)

	if file, err := store.TelegramFile.GetByLocationID(ctx, locationID); err != nil {
		return "", nil, nil, fmt.Errorf("failed to search for Telegram file by location ID: %w", err)
	} else if file != nil {
		t.inner.fileInfo.Size, t.inner.fileInfo.MimeType = file.Size, file.MIMEType
		return file.MXC, nil, &t.inner.fileInfo, nil
	}

	data, _, err := t.Download(ctx)
	if err != nil {
		return "", nil, nil, fmt.Errorf("downloading file failed: %w", err)
	}

	if t.inner.animatedStickerConfig != nil && t.inner.fileInfo.MimeType == "application/x-tgsticker" {
		converted := t.inner.animatedStickerConfig.convert(ctx, data)
		data = converted.Data
		t.inner.fileInfo.MimeType = converted.MIMEType
		t.inner.fileInfo.Width = converted.Width
		t.inner.fileInfo.Height = converted.Height
		t.inner.fileInfo.Size = len(data)

		if len(converted.ThumbnailData) > 0 {
			thumbnailMXC, thumbnailFileInfo, err := intent.UploadMedia(ctx, t.inner.roomID, converted.ThumbnailData, t.inner.filename, converted.ThumbnailMIMEType)
			if err != nil {
				log.Err(err).Msg("failed to upload animated sticker thumbnail to Matrix")
			} else {
				t.inner = t.inner.WithThumbnail(thumbnailMXC, thumbnailFileInfo, &event.FileInfo{
					MimeType: converted.ThumbnailMIMEType,
					Width:    converted.Width,
					Height:   converted.Height,
					Size:     len(converted.ThumbnailData),
				})
			}
		}
	}

	mxc, encryptedFileInfo, err = intent.UploadMedia(ctx, t.inner.roomID, data, t.inner.filename, t.inner.fileInfo.MimeType)
	if err != nil {
		return "", nil, nil, fmt.Errorf("failed to upload media to Matrix: %w", err)
	}

	// If it's an unencrypted file, cache the MXC URI corresponding to the
	// location ID.
	if len(mxc) > 0 {
		file := store.TelegramFile.New()
		file.LocationID = locationID
		file.MXC = mxc
		file.Size = t.inner.fileInfo.Size
		file.MIMEType = t.inner.fileInfo.MimeType
		if err = file.Insert(ctx); err != nil {
			log.Err(err).Msg("failed to insert Telegram file into database")
		}
	}
	return mxc, encryptedFileInfo, &t.inner.fileInfo, nil
}

// Download downloads the media from Telegram.
func (t *ReadyTransferer) Download(ctx context.Context) ([]byte, *event.FileInfo, error) {
	// TODO convert entire function to streaming? Maybe at least stream to file?
	var buf bytes.Buffer
	storageFileTypeClass, err := downloader.NewDownloader().Download(t.inner.client, t.loc).Stream(ctx, &buf)
	if err != nil {
		return nil, nil, err
	}
	if t.inner.fileInfo.MimeType == "" {
		switch storageFileTypeClass.(type) {
		case *tg.StorageFileJpeg:
			t.inner.fileInfo.MimeType = "image/jpeg"
		case *tg.StorageFileGif:
			t.inner.fileInfo.MimeType = "image/gif"
		case *tg.StorageFilePng:
			t.inner.fileInfo.MimeType = "image/png"
		case *tg.StorageFilePdf:
			t.inner.fileInfo.MimeType = "application/pdf"
		case *tg.StorageFileMp3:
			t.inner.fileInfo.MimeType = "audio/mp3"
		case *tg.StorageFileMov:
			t.inner.fileInfo.MimeType = "video/quicktime"
		case *tg.StorageFileMp4:
			t.inner.fileInfo.MimeType = "video/mp4"
		case *tg.StorageFileWebp:
			t.inner.fileInfo.MimeType = "image/webp"
		default:
			t.inner.fileInfo.MimeType = http.DetectContentType(buf.Bytes())
		}
	}
	t.inner.fileInfo.Size = buf.Len()

	if t.inner.animatedStickerConfig != nil {
		detected := http.DetectContentType(buf.Bytes())
		if detected == "application/x-tgsticker" || detected == "application/x-gzip" {
			if unzipped, err := gnuzip.MaybeGUnzip(buf.Bytes()); err != nil {
				zerolog.Ctx(ctx).Err(err).Msg("failed to unzip animated sticker")
			} else {
				converted := t.inner.animatedStickerConfig.convert(ctx, unzipped)
				t.inner.fileInfo.MimeType = converted.MIMEType
				t.inner.fileInfo.Size = len(converted.Data)
				return converted.Data, &t.inner.fileInfo, nil
			}
		}
	}

	return buf.Bytes(), &t.inner.fileInfo, nil
}

// DirectDownloadURL returns the direct download URL for the media.
func (t *ReadyTransferer) DirectDownloadURL(ctx context.Context, loggedInUserID int64, portal *bridgev2.Portal, msgID int, thumbnail bool, telegramMediaID int64) (id.ContentURIString, *event.FileInfo, error) {
	peerType, chatID, err := ids.ParsePortalID(portal.ID)
	if err != nil {
		return "", nil, err
	}
	mediaID, err := ids.DirectMediaInfo{
		PeerType:        peerType,
		ChatID:          chatID,
		ReceiverID:      loggedInUserID,
		MessageID:       int64(msgID),
		Thumbnail:       thumbnail,
		TelegramMediaID: telegramMediaID,
	}.AsMediaID()
	if err != nil {
		return "", nil, err
	}
	mxc, err := portal.Bridge.Matrix.GenerateContentURI(ctx, mediaID)
	return mxc, &t.inner.fileInfo, err
}
