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

	"go.mau.fi/util/lottie"

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

type AnimatedStickerConfig struct {
	Target          string `yaml:"target"`
	ConvertFromWebm bool   `yaml:"convert_from_webm"`
	Args            struct {
		Width  int `yaml:"width"`
		Height int `yaml:"height"`
		FPS    int `yaml:"fps"`
	} `yaml:"args"`
}

func (c AnimatedStickerConfig) TGSConvert() bool {
	return c.Target == "gif" || c.Target == "png"
}

func (c AnimatedStickerConfig) WebmConvert() bool {
	return c.ConvertFromWebm && c.Target != "webm"
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

func (t *Transferer) WithMIMEType(mimeType string) *Transferer {
	t.fileInfo.MimeType = mimeType
	return t
}

// WithStickerConfig sets the animated sticker config for the [Transferer].
func (t *Transferer) WithStickerConfig(cfg AnimatedStickerConfig) *Transferer {
	t.animatedStickerConfig = &cfg
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
		t.fileInfo.MimeType = document.GetMimeType()
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
func (t *Transferer) WithUserPhoto(user *tg.User, photoID int64) *ReadyTransferer {
	return &ReadyTransferer{
		inner: t,
		loc: &tg.InputPeerPhotoFileLocation{
			Peer:    &tg.InputPeerUser{UserID: user.GetID()},
			PhotoID: photoID,
			Big:     true,
		},
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

	if t.inner.animatedStickerConfig != nil {
		if lottie.Supported() && t.inner.animatedStickerConfig.TGSConvert() && t.inner.fileInfo.MimeType == "application/x-tgsticker" {
			newData, err := lottie.ConvertBytes(ctx, data,
				t.inner.animatedStickerConfig.Target,
				t.inner.animatedStickerConfig.Args.Width,
				t.inner.animatedStickerConfig.Args.Height,
				fmt.Sprintf("%d", t.inner.animatedStickerConfig.Args.FPS))
			if err != nil {
				log.Err(err).Msg("failed to convert animated sticker")
			} else {
				data = newData
				t.inner.fileInfo.Size = len(data)
				t.inner.fileInfo.MimeType = fmt.Sprintf("image/%s", t.inner.animatedStickerConfig.Target)
			}
			// TODO support ffmpeg conversion
			// } else if ffmpeg.Supported() && t.Config.WebmConvert() && mimeType == "video/webm" {
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
func (t *ReadyTransferer) Download(ctx context.Context) (data []byte, fileInfo *event.FileInfo, err error) {
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
	t.inner.fileInfo.Size = len(data)
	return buf.Bytes(), &t.inner.fileInfo, nil
}

// DirectDownloadURL returns the direct download URL for the media.
func (t *ReadyTransferer) DirectDownloadURL(ctx context.Context, portal *bridgev2.Portal, msgID int, thumbnail bool) (id.ContentURIString, *event.FileInfo, error) {
	peerType, chatID, err := ids.ParsePortalID(portal.ID)
	if err != nil {
		return "", nil, err
	}
	mediaID, err := ids.DirectMediaInfo{
		PeerType:  peerType,
		ChatID:    chatID,
		MessageID: int64(msgID),
		Thumbnail: thumbnail,
	}.AsMediaID()
	if err != nil {
		return "", nil, err
	}
	mxc, err := portal.Bridge.Matrix.GenerateContentURI(ctx, mediaID)
	return mxc, &t.inner.fileInfo, err
}
