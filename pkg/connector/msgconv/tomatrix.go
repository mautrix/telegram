package msgconv

import (
	"bytes"
	"context"
	"fmt"
	"time"

	"github.com/gotd/td/telegram"
	"github.com/gotd/td/telegram/downloader"
	"github.com/gotd/td/tg"
	"github.com/rs/zerolog"
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
			if media.GetSpoiler() {
				// TODO do something
				fmt.Printf("SPOILER\n")
			}
			if p, ok := media.GetPhoto(); ok {
				switch photo := p.(type) {
				case *tg.Photo:
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
					contentType := "application/octet-stream"
					switch storageFileTypeClass.(type) {
					case *tg.StorageFileJpeg:
						contentType = "image/jpeg"
					case *tg.StorageFileGif:
						contentType = "image/gif"
					case *tg.StorageFilePng:
						contentType = "image/png"
					case *tg.StorageFilePdf:
						contentType = "application/pdf"
					case *tg.StorageFileMp3:
						contentType = "audio/mp3"
					case *tg.StorageFileMov:
						contentType = "video/quicktime"
					case *tg.StorageFileMp4:
						contentType = "video/mp4"
					case *tg.StorageFileWebp:
						contentType = "image/webp"
					}

					mxcURI, encryptedFileInfo, err := intent.UploadMedia(ctx, "", buf.Bytes(), "", contentType)
					if err != nil {
						return nil, err
					}
					cm.Parts = append(cm.Parts, &bridgev2.ConvertedMessagePart{
						ID:   networkid.PartID("photo"),
						Type: event.EventMessage,
						Content: &event.MessageEventContent{
							MsgType: event.MsgImage,
							// Body:    filename,
							URL:  mxcURI,
							File: encryptedFileInfo,
						},
					})

				default:
					log.Error().Type("msg", msg).Msg("Unhandled photo type")
				}
			}
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
