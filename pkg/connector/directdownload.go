// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2025 Sumner Evans
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

package connector

import (
	"context"
	"fmt"
	"io"

	"github.com/gotd/td/tg"
	"github.com/rs/zerolog"
	"maunium.net/go/mautrix"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/mediaproxy"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/connector/media"
)

var _ bridgev2.DirectMediableNetwork = (*TelegramConnector)(nil)

func (tc *TelegramConnector) Download(ctx context.Context, mediaID networkid.MediaID, params map[string]string) (mediaproxy.GetMediaResponse, error) {
	info, err := ids.ParseDirectMediaInfo(mediaID)
	if err != nil {
		return nil, err
	}
	log := zerolog.Ctx(ctx).With().
		Str("component", "direct download").
		Any("info", info).
		Logger()
	ctx = log.WithContext(ctx)
	log.Info().Any("info", info).Msg("handling direct download")

	// TODO have an in-memory cache for media references?

	userLogin, err := tc.Bridge.GetExistingUserLoginByID(ctx, ids.MakeUserLoginID(info.UserID))
	if err != nil {
		if info.PeerType != ids.PeerTypeChannel {
			return nil, fmt.Errorf("failed to get user login: %w", err)
		}

		logins, err := tc.Bridge.GetUserLoginsInPortal(ctx, ids.PeerTypeChannel.InternalAsPortalKey(info.PeerID, ""))
		if err != nil {
			return nil, err
		} else if len(logins) == 0 {
			return nil, fmt.Errorf("no user logins in the portal (%s %d)", ids.PeerTypeChannel, info.PeerID)
		}
		userLogin = logins[0]
	}

	if userLogin == nil || userLogin.Client == nil {
		log.Error().Msg("User does not have a login or client")
		return nil, mautrix.MForbidden.WithMessage("User not logged in")
	}

	client := userLogin.Client.(*TelegramClient)

	if !client.IsLoggedIn() {
		log.Error().Msg("User is not logged in, returning media proxy error")
		return nil, mautrix.MForbidden.WithMessage("User not logged in")
	}

	transferer := media.NewTransferer(client.client.API())
	var readyTransferer *media.ReadyTransferer

	if info.MessageID > 0 {
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
			accessHash, err = client.ScopedStore.GetAccessHash(ctx, ids.PeerTypeChannel, info.PeerID)
			if err != nil {
				return nil, fmt.Errorf("failed to get channel access hash: %w", err)
			} else {
				messages, err = APICallWithUpdates(ctx, client, func() (tg.ModifiedMessagesMessages, error) {
					m, err := client.client.API().ChannelsGetMessages(ctx, &tg.ChannelsGetMessagesRequest{
						Channel: &tg.InputChannel{ChannelID: info.PeerID, AccessHash: accessHash},
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
	} else if info.PeerType == ids.PeerTypeUser {
		readyTransferer, err = transferer.WithUserPhoto(ctx, client.ScopedStore, info.PeerID, info.ID)
		if err != nil {
			return nil, fmt.Errorf("failed to create user photo transferer: %w", err)
		}
	} else if info.PeerType == ids.PeerTypeChat {
		fullChat, err := APICallWithUpdates(ctx, client, func() (*tg.MessagesChatFull, error) {
			return client.client.API().MessagesGetFullChat(ctx, info.PeerID)
		})
		if err != nil {
			return nil, err
		}

		chatFull, ok := fullChat.FullChat.(*tg.ChatFull)
		if !ok {
			return nil, fmt.Errorf("full chat is %T not *tg.ChatFull", fullChat.FullChat)
		}

		// FIXME: this is basically a not found error
		if photoID := chatFull.ChatPhoto.GetID(); photoID != info.ID {
			return nil, fmt.Errorf("photo id mismatch: %d != %d", photoID, info.ID)
		}

		readyTransferer = transferer.WithPhoto(chatFull.ChatPhoto)
	} else if info.PeerType == ids.PeerTypeChannel {
		accessHash, err := client.ScopedStore.GetAccessHash(ctx, ids.PeerTypeChannel, info.PeerID)
		if err != nil {
			return nil, fmt.Errorf("failed to get channel access hash: %w", err)
		}

		readyTransferer = transferer.WithChannelPhoto(info.PeerID, accessHash, info.ID)
	}

	if readyTransferer == nil {
		return nil, fmt.Errorf("invalid combination of direct media keys")
	}

	r, mimeType, size, err := readyTransferer.Stream(ctx)
	if err != nil {
		log.Err(err).Msg("failed to download media")
		return nil, err
	}

	log.Debug().
		Str("mime_type", mimeType).
		Int("size", size).
		Msg("Downloaded media successfully")

	return &mediaproxy.GetMediaResponseData{
		Reader:        io.NopCloser(r),
		ContentType:   mimeType,
		ContentLength: int64(size),
	}, nil
}

func (tg *TelegramConnector) SetUseDirectMedia() {
	tg.useDirectMedia = true
}
