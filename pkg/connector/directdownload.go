package connector

import (
	"bytes"
	"context"
	"fmt"
	"io"

	"github.com/gotd/td/tg"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/mediaproxy"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	conmedia "go.mau.fi/mautrix-telegram/pkg/connector/media"
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
		for _, message := range m.GetMessages() {
			if msg, ok := message.(*tg.Message); ok && msg.ID == int(info.MessageID) {
				media = msg.Media
				break
			}
		}
	}

	switch media := media.(type) {
	case *tg.MessageMediaPhoto:
		data, mimeType, err := conmedia.DownloadPhoto(ctx, client.client.API(), media)
		if err != nil {
			return nil, err
		}

		return &mediaproxy.GetMediaResponseData{
			Reader:        io.NopCloser(bytes.NewBuffer(data)),
			ContentType:   mimeType,
			ContentLength: int64(len(data)),
		}, nil

		// TODO all of these
		// case *tg.MessageMediaGeo: // messageMediaGeo#56e0d474
		// case *tg.MessageMediaContact: // messageMediaContact#70322949
		// case *tg.MessageMediaUnsupported: // messageMediaUnsupported#9f84f49e
		// case *tg.MessageMediaDocument: // messageMediaDocument#4cf4d72d
		// case *tg.MessageMediaWebPage: // messageMediaWebPage#ddf10c3b
		// case *tg.MessageMediaVenue: // messageMediaVenue#2ec0533f
		// case *tg.MessageMediaGame: // messageMediaGame#fdb19008
		// case *tg.MessageMediaInvoice: // messageMediaInvoice#f6a548d3
		// case *tg.MessageMediaGeoLive: // messageMediaGeoLive#b940c666
		// case *tg.MessageMediaPoll: // messageMediaPoll#4bd6e798
		// case *tg.MessageMediaDice: // messageMediaDice#3f7ee58b
		// case *tg.MessageMediaStory: // messageMediaStory#68cb6283
		// case *tg.MessageMediaGiveaway: // messageMediaGiveaway#daad85b0
		// case *tg.MessageMediaGiveawayResults: // messageMediaGiveawayResults#c6991068
	default:
		return nil, fmt.Errorf("unhandled media type %T", media)
	}
}

func (tg *TelegramConnector) SetUseDirectMedia() {
	tg.useDirectMedia = true
}
