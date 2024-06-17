package msgconv

import (
	"bytes"
	"context"
	"fmt"
	"net/http"
	"time"

	"github.com/gotd/td/telegram"
	"github.com/gotd/td/telegram/downloader"
	"github.com/gotd/td/tg"
	"github.com/rs/zerolog"
	"go.mau.fi/util/exmime"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/event"
)

type MessageConverter struct {
	client *telegram.Client
}

func NewMessageConverter(client *telegram.Client) *MessageConverter {
	return &MessageConverter{client: client}
}

func (mc *MessageConverter) ToMatrix(ctx context.Context, portal *bridgev2.Portal, intent bridgev2.MatrixAPI, msg *tg.Message) (*bridgev2.ConvertedMessage, error) {
	log := zerolog.Ctx(ctx).With().Str("conversion_direction", "to_matrix").Logger()

	cm := &bridgev2.ConvertedMessage{
		Timestamp: time.Unix(int64(msg.Date), 0),
	}
	if msg.Message != "" {
		cm.Parts = append(cm.Parts, &bridgev2.ConvertedMessagePart{
			ID:      networkid.PartID("caption"),
			Type:    event.EventMessage,
			Content: &event.MessageEventContent{MsgType: event.MsgText, Body: msg.Message},
		})
	}
	if m, ok := msg.GetMedia(); ok {
		switch media := m.(type) {
		case *tg.MessageMediaPhoto:
			p, ok := media.GetPhoto()
			if !ok {
				return nil, fmt.Errorf("photo message sent without a photo")
			}
			photo, ok := p.(*tg.Photo)
			if !ok {
				return nil, fmt.Errorf("unrecognized photo type %T", p)
			}

			largest := getLargestPhotoSize(photo.GetSizes())
			file := tg.InputPhotoFileLocation{
				ID:            photo.GetID(),
				AccessHash:    photo.GetAccessHash(),
				FileReference: photo.GetFileReference(),
				ThumbSize:     largest.GetType(),
			}

			// TODO convert to streaming directly into UploadMedia
			var buf bytes.Buffer
			storageFileTypeClass, err := downloader.NewDownloader().Download(mc.client.API(), &file).Stream(ctx, &buf)
			if err != nil {
				return nil, err
			}
			var mimeType string
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
			var filename string
			if _, ok := media.GetTTLSeconds(); ok {
				// TODO set the ttl on the converted message
				filename = "disappearing_image" + exmime.ExtensionFromMimetype(mimeType)
			} else {
				filename = "image" + exmime.ExtensionFromMimetype(mimeType)
			}

			mxcURI, encryptedFileInfo, err := intent.UploadMedia(ctx, "", buf.Bytes(), filename, mimeType)
			if err != nil {
				return nil, err
			}

			extra := map[string]any{}
			if media.GetSpoiler() {
				// See: https://github.com/matrix-org/matrix-spec-proposals/pull/3725
				extra["town.robin.msc3725.content_warning"] = map[string]any{
					"type": "town.robin.msc3725.spoiler",
				}
			}

			cm.Parts = append(cm.Parts, &bridgev2.ConvertedMessagePart{
				ID:   networkid.PartID("photo"),
				Type: event.EventMessage,
				Content: &event.MessageEventContent{
					MsgType: event.MsgImage,
					Body:    filename,
					URL:     mxcURI,
					File:    encryptedFileInfo,
				},
				Extra: extra,
			})

		case *tg.MessageMediaGeo: // messageMediaGeo#56e0d474
		case *tg.MessageMediaContact: // messageMediaContact#70322949
		case *tg.MessageMediaUnsupported: // messageMediaUnsupported#9f84f49e
		case *tg.MessageMediaDocument: // messageMediaDocument#4cf4d72d
		case *tg.MessageMediaWebPage: // messageMediaWebPage#ddf10c3b
		case *tg.MessageMediaVenue: // messageMediaVenue#2ec0533f
		case *tg.MessageMediaGame: // messageMediaGame#fdb19008
		case *tg.MessageMediaInvoice: // messageMediaInvoice#f6a548d3
		case *tg.MessageMediaGeoLive: // messageMediaGeoLive#b940c666
		case *tg.MessageMediaPoll: // messageMediaPoll#4bd6e798
		case *tg.MessageMediaDice: // messageMediaDice#3f7ee58b
		case *tg.MessageMediaStory: // messageMediaStory#68cb6283
		case *tg.MessageMediaGiveaway: // messageMediaGiveaway#daad85b0
		case *tg.MessageMediaGiveawayResults: // messageMediaGiveawayResults#c6991068
		default:
			log.Error().Type("msg", msg).Msg("Unhandled media type")
		}
	}
	return cm, nil
}

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
