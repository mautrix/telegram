package connector

import (
	"bytes"
	"context"
	"fmt"
	"io"

	"github.com/gotd/td/tg"
	"github.com/rs/zerolog"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/mediaproxy"

	"go.mau.fi/mautrix-telegram/pkg/connector/download"
	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
)

var _ bridgev2.DirectMediableNetwork = (*TelegramConnector)(nil)

type getMessages interface {
	GetMessages() []tg.MessageClass
}

func (tc *TelegramConnector) Download(ctx context.Context, mediaID networkid.MediaID) (mediaproxy.GetMediaResponse, error) {
	info, err := ids.ParseDirectMediaInfo(mediaID)
	if err != nil {
		return nil, err
	}
	log := zerolog.Ctx(ctx).With().
		Str("component", "direct download").
		Any("info", info).
		Logger()
	ctx = log.WithContext(ctx)
	log.Info().Msg("handling direct download")

	logins, err := tc.Bridge.GetUserLoginsInPortal(ctx, info.PeerType.AsPortalKey(info.ChatID))
	if err != nil {
		return nil, err
	} else if len(logins) == 0 {
		return nil, fmt.Errorf("no user logins in the portal (%s %d)", info.PeerType, info.ChatID)
	}

	client := logins[0].Client.(*TelegramClient)
	var messages tg.MessagesMessagesClass
	switch info.PeerType {
	case ids.PeerTypeUser, ids.PeerTypeChat:
		messages, err = client.client.API().MessagesGetMessages(ctx, []tg.InputMessageClass{
			&tg.InputMessageID{ID: int(info.MessageID)},
		})
	case ids.PeerTypeChannel:
		// TODO test this
		messages, err = client.client.API().ChannelsGetMessages(ctx, &tg.ChannelsGetMessagesRequest{
			Channel: &tg.InputChannel{ChannelID: info.ChatID},
			ID: []tg.InputMessageClass{
				&tg.InputMessageID{ID: int(info.MessageID)},
			},
		})
	default:
		return nil, fmt.Errorf("unknown peer type %s", info.PeerType)
	}
	if err != nil {
		return nil, err
	}

	var media tg.MessageMediaClass
	if m, ok := messages.(getMessages); !ok {
		return nil, fmt.Errorf("unknown message type")
	} else {
		var found bool
		for _, message := range m.GetMessages() {
			if msg, ok := message.(*tg.Message); ok && msg.ID == int(info.MessageID) {
				media = msg.Media
				found = true
				break
			}
		}
		if !found {
			return nil, fmt.Errorf("no media found with ID %d", info.MessageID)
		}
	}

	var data []byte
	var mimeType string
	switch media := media.(type) {
	case *tg.MessageMediaPhoto:
		data, mimeType, err = download.DownloadPhotoMedia(ctx, client.client.API(), media)
	case *tg.MessageMediaDocument:
		document, ok := media.Document.(*tg.Document)
		if !ok {
			return nil, fmt.Errorf("unrecognized document type %T", media.Document)
		}
		mimeType = document.GetMimeType()
		data, err = download.DownloadDocument(ctx, client.client.API(), document)

		// TODO all of these
		// case *tg.MessageMediaUnsupported: // messageMediaUnsupported#9f84f49e
		// case *tg.MessageMediaDocument: // messageMediaDocument#4cf4d72d
		// case *tg.MessageMediaGame: // messageMediaGame#fdb19008
		// case *tg.MessageMediaInvoice: // messageMediaInvoice#f6a548d3
		// case *tg.MessageMediaPoll: // messageMediaPoll#4bd6e798
		// case *tg.MessageMediaDice: // messageMediaDice#3f7ee58b
		// case *tg.MessageMediaStory: // messageMediaStory#68cb6283
		// case *tg.MessageMediaGiveaway: // messageMediaGiveaway#daad85b0
		// case *tg.MessageMediaGiveawayResults: // messageMediaGiveawayResults#c6991068
	default:
		return nil, fmt.Errorf("unhandled media type %T", media)
	}
	if err != nil {
		return nil, err
	}

	return &mediaproxy.GetMediaResponseData{
		Reader:        io.NopCloser(bytes.NewBuffer(data)),
		ContentType:   mimeType,
		ContentLength: int64(len(data)),
	}, nil
}

func (tg *TelegramConnector) SetUseDirectMedia() {
	tg.useDirectMedia = true
}
