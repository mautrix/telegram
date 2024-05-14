package msgconv

import (
	"bytes"
	"context"
	"fmt"
	"os"

	"github.com/gotd/td/telegram/downloader"
	"github.com/gotd/td/tg"
	"maunium.net/go/mautrix/event"
)

type ConvertedMessage struct {
	Parts []*ConvertedMessagePart
}

type ConvertedMessagePart struct {
	Type    event.Type
	Content *event.MessageEventContent
	Extra   map[string]any
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

func (mc *MessageConverter) downloadFile(ctx context.Context, file tg.InputFileLocationClass) ([]byte, error) {
	var buf bytes.Buffer
	_, err := downloader.NewDownloader().Download(mc.Client.API(), file).Stream(ctx, &buf)
	return buf.Bytes(), err
}

func (mc *MessageConverter) ToMatrix(ctx context.Context, msg tg.MessageClass) *ConvertedMessage {
	log := mc.getLogger(ctx).With().Str("action", "to_matrix").Logger()
	cm := &ConvertedMessage{
		Parts: make([]*ConvertedMessagePart, 0),
	}

	switch v := msg.(type) {
	case *tg.Message:
		if v.Message != "" {
			converted := ConvertedMessagePart{
				Type: event.EventMessage,
				Content: &event.MessageEventContent{
					MsgType: event.MsgText,
					Body:    v.Message,
				},
			}
			cm.Parts = append(cm.Parts, &converted)
		}

		if m, ok := v.GetMedia(); ok {
			switch media := m.(type) {
			case *tg.MessageMediaPhoto: // messageMediaPhoto#695150d7
				fmt.Printf("photo %v\n", media)
				if media.GetSpoiler() {
					// TODO do something
					fmt.Printf("SPOILER\n")
				}
				if p, ok := media.GetPhoto(); ok {
					switch photo := p.(type) {
					case *tg.Photo: // photo#fb197a65
						fmt.Printf("photo: %v\n", photo)

						largest := getLargestPhotoSize(photo.GetSizes())
						file := tg.InputPhotoFileLocation{
							ID:            photo.GetID(),
							AccessHash:    photo.GetAccessHash(),
							FileReference: photo.GetFileReference(),
							ThumbSize:     largest.GetType(),
						}

						data, err := mc.downloadFile(ctx, &file)
						if err != nil {
							panic(err)
						}
						err = os.WriteFile("/home/sumner/tmp/test.jpg", data, 0644)
						if err != nil {
							panic(err)
						}
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

	case *tg.MessageService:
		fmt.Printf("%v\n", v)
	default:
		log.Error().Type("msg", msg).Msg("Unhandled message type")
	}

	return cm
}
