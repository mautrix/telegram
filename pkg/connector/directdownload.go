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

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/connector/media"
)

var _ bridgev2.DirectMediableNetwork = (*TelegramConnector)(nil)

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

	// TODO have an in-memory cache for media references?

	userLogin, err := tc.Bridge.GetExistingUserLoginByID(ctx, ids.MakeUserLoginID(info.ReceiverID))
	if err != nil {
		if info.PeerType != ids.PeerTypeChannel {
			return nil, fmt.Errorf("failed to get user login: %w", err)
		}

		logins, err := tc.Bridge.GetUserLoginsInPortal(ctx, ids.PeerTypeChannel.InternalAsPortalKey(info.ChatID, ""))
		if err != nil {
			return nil, err
		} else if len(logins) == 0 {
			return nil, fmt.Errorf("no user logins in the portal (%s %d)", ids.PeerTypeChannel, info.ChatID)
		}
		userLogin = logins[0]
	}

	client := userLogin.Client.(*TelegramClient)
	var messages tg.ModifiedMessagesMessages
	switch info.PeerType {
	case ids.PeerTypeUser, ids.PeerTypeChat:
		messages, err = APICallWithUpdates(ctx, client, func() (tg.ModifiedMessagesMessages, error) {
			m, err := client.client.API().MessagesGetMessages(ctx, []tg.InputMessageClass{
				&tg.InputMessageID{ID: int(info.MessageID)},
			})
			if err != nil {
				return nil, err
			} else if messages, ok := m.(tg.ModifiedMessagesMessages); !ok {
				return nil, fmt.Errorf("unsupported messages type %T", messages)
			} else {
				return messages, nil
			}
		})
	case ids.PeerTypeChannel:
		var accessHash int64
		accessHash, err = client.ScopedStore.GetAccessHash(ctx, ids.PeerTypeChannel, info.ChatID)
		if err != nil {
			return nil, fmt.Errorf("failed to get channel access hash: %w", err)
		} else {
			messages, err = APICallWithUpdates(ctx, client, func() (tg.ModifiedMessagesMessages, error) {
				m, err := client.client.API().ChannelsGetMessages(ctx, &tg.ChannelsGetMessagesRequest{
					Channel: &tg.InputChannel{ChannelID: info.ChatID, AccessHash: accessHash},
					ID: []tg.InputMessageClass{
						&tg.InputMessageID{ID: int(info.MessageID)},
					},
				})
				if err != nil {
					return nil, err
				} else if messages, ok := m.(tg.ModifiedMessagesMessages); !ok {
					return nil, fmt.Errorf("unsupported messages type %T", messages)
				} else {
					return messages, nil
				}
			})
		}
	default:
		return nil, fmt.Errorf("unknown peer type %s", info.PeerType)
	}
	if err != nil {
		return nil, fmt.Errorf("failed to get messages for %+v: %w", info, err)
	}

	var msgMedia tg.MessageMediaClass
	if len(messages.GetMessages()) != 1 {
		return nil, fmt.Errorf("wrong number of messages retrieved %d", len(messages.GetMessages()))
	} else if msg, ok := messages.GetMessages()[0].(*tg.Message); !ok {
		return nil, fmt.Errorf("message was of the wrong type %s", messages.GetMessages()[0].TypeName())
	} else if msg.ID != int(info.MessageID) {
		return nil, fmt.Errorf("no media found with ID %d", info.MessageID)
	} else {
		msgMedia = msg.Media
	}

	transferer := media.NewTransferer(client.client.API())
	var readyTransferer *media.ReadyTransferer
	switch msgMedia := msgMedia.(type) {
	case *tg.MessageMediaPhoto:
		log.Debug().
			Int64("photo_id", msgMedia.Photo.GetID()).
			Msg("downloading photo")
		readyTransferer = transferer.WithPhoto(msgMedia.Photo)
	case *tg.MessageMediaDocument:
		document, ok := msgMedia.Document.(*tg.Document)
		if !ok {
			return nil, fmt.Errorf("unknown document type %T", msgMedia.Document)
		}
		var isSticker bool
		for _, attr := range document.GetAttributes() {
			if attr.TypeID() == tg.DocumentAttributeStickerTypeID {
				transferer = transferer.WithStickerConfig(tc.Config.AnimatedSticker)
				isSticker = true
			}
		}

		log.Debug().
			Int64("document_id", msgMedia.Document.GetID()).
			Bool("is_sticker", isSticker).
			Msg("downloading photo")
		readyTransferer = transferer.WithDocument(msgMedia.Document, info.Thumbnail)
	default:
		return nil, fmt.Errorf("unhandled media type %T", msgMedia)
	}

	data, fileInfo, err := readyTransferer.Download(ctx)
	if err != nil {
		log.Err(err).Msg("failed to download media")
		return nil, err
	}

	log.Debug().
		Str("mime_type", fileInfo.MimeType).
		Int("size", fileInfo.Size).
		Msg("Downloaded media successfully")

	return &mediaproxy.GetMediaResponseData{
		Reader:        io.NopCloser(bytes.NewBuffer(data)),
		ContentType:   fileInfo.MimeType,
		ContentLength: int64(fileInfo.Size),
	}, nil
}

func (tg *TelegramConnector) SetUseDirectMedia() {
	tg.useDirectMedia = true
}
