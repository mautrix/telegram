package media

import (
	"context"
	"fmt"

	"github.com/gotd/td/telegram/downloader"
	"github.com/gotd/td/tg"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/event"
	"maunium.net/go/mautrix/id"

	"go.mau.fi/util/lottie"

	"go.mau.fi/mautrix-telegram/pkg/connector/store"
)

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

type Transferer struct {
	RoomID    id.RoomID
	Filename  string
	IsSticker bool
	Config    AnimatedStickerConfig
}

func NewTransferer(cfg AnimatedStickerConfig) *Transferer {
	return &Transferer{Config: cfg}
}

func (t *Transferer) WithRoomID(roomID id.RoomID) *Transferer {
	t.RoomID = roomID
	return t
}

func (t *Transferer) WithFilename(filename string) *Transferer {
	t.Filename = filename
	return t
}

func (t *Transferer) WithIsSticker(isSticker bool) *Transferer {
	t.IsSticker = isSticker
	return t
}

func (t *Transferer) Transfer(ctx context.Context, store *store.Container, client downloader.Client, intent bridgev2.MatrixAPI, loc tg.InputFileLocationClass) (mxc id.ContentURIString, encryptedFileInfo *event.EncryptedFileInfo, size int, mimeType string, err error) {
	locationID := getLocationID(loc)
	if file, err := store.TelegramFile.GetByLocationID(ctx, locationID); err != nil {
		return "", nil, 0, "", fmt.Errorf("failed to search for Telegram file by location ID: %w", err)
	} else if file != nil {
		return file.MXC, nil, file.Size, file.MIMEType, nil
	}

	var data []byte
	data, mimeType, err = DownloadFileLocation(ctx, client, loc)
	if err != nil {
		return "", nil, 0, "", fmt.Errorf("downloading file failed: %w", err)
	}

	if t.IsSticker {
		if lottie.Supported() && t.Config.TGSConvert() && mimeType == "application/x-gzip" {
			data, err = lottie.ConvertBytes(ctx, data, t.Config.Target, t.Config.Args.Width, t.Config.Args.Height, fmt.Sprintf("%d", t.Config.Args.FPS))
			if err != nil {
				return "", nil, 0, "", err
			}
			mimeType = fmt.Sprintf("image/%s", t.Config.Target)
			// TODO support ffmpeg conversion
			// } else if ffmpeg.Supported() && t.Config.WebmConvert() && mimeType == "video/webm" {
		}
	}

	mxcURI, encryptedFileInfo, err := intent.UploadMedia(ctx, t.RoomID, data, t.Filename, mimeType)
	if err != nil {
		return "", nil, 0, "", err
	}
	if len(mxcURI) > 0 {
		file := store.TelegramFile.New()
		file.LocationID = locationID
		file.MXC = mxcURI
		file.Size = len(data)
		file.MIMEType = mimeType
		// TODO width, height, thumbnail?
		if err = file.Insert(ctx); err != nil {
			return "", nil, 0, "", fmt.Errorf("failed to insert Telegram file into database: %w", err)
		}
	}
	return mxcURI, encryptedFileInfo, len(data), mimeType, nil
}
