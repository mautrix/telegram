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

	"github.com/rs/zerolog"
	"maunium.net/go/mautrix"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/mediaproxy"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/connector/media"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

var _ bridgev2.DirectMediableNetwork = (*TelegramConnector)(nil)

func (tc *TelegramClient) refetchMedia(ctx context.Context, peerType ids.PeerType, peerID int64, msgID int) (tg.MessageMediaClass, error) {
	var messages tg.ModifiedMessagesMessages
	var err error
	switch peerType {
	case ids.PeerTypeUser, ids.PeerTypeChat:
		messages, err = APICallWithUpdates(ctx, tc, func() (tg.ModifiedMessagesMessages, error) {
			m, err := tc.client.API().MessagesGetMessages(ctx, []tg.InputMessageClass{
				&tg.InputMessageID{ID: msgID},
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
		accessHash, err = tc.ScopedStore.GetAccessHash(ctx, ids.PeerTypeChannel, peerID)
		if err != nil {
			return nil, fmt.Errorf("failed to get channel access hash: %w", err)
		}
		messages, err = APICallWithUpdates(ctx, tc, func() (tg.ModifiedMessagesMessages, error) {
			m, err := tc.client.API().ChannelsGetMessages(ctx, &tg.ChannelsGetMessagesRequest{
				Channel: &tg.InputChannel{ChannelID: peerID, AccessHash: accessHash},
				ID: []tg.InputMessageClass{
					&tg.InputMessageID{ID: msgID},
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
	default:
		return nil, fmt.Errorf("unknown peer type %s", peerType)
	}
	if err != nil {
		return nil, fmt.Errorf("failed to get message %d/%d for media info: %w", peerID, msgID, err)
	}

	if len(messages.GetMessages()) != 1 {
		return nil, fmt.Errorf("wrong number of messages retrieved %d", len(messages.GetMessages()))
	} else if msg, ok := messages.GetMessages()[0].(*tg.Message); !ok {
		return nil, fmt.Errorf("message was of the wrong type %s", messages.GetMessages()[0].TypeName())
	} else if msg.ID != msgID {
		return nil, fmt.Errorf("no media found with ID %d", msgID)
	} else {
		return msg.Media, nil
	}
}

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

		logins, err := tc.Bridge.GetUserLoginsInPortal(ctx, ids.InternalMakePortalKey(ids.PeerTypeChannel, info.PeerID, 0, ""))
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
		rawMsgMedia, err := client.refetchMedia(ctx, info.PeerType, info.PeerID, int(info.MessageID))
		if err != nil {
			return nil, fmt.Errorf("failed to refetch media message: %w", err)
		}

		switch msgMedia := rawMsgMedia.(type) {
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
				Msg("downloading document")
			readyTransferer = transferer.WithDocument(msgMedia.Document, info.Thumbnail)
		case *tg.MessageMediaWebPage:
			webpage, ok := msgMedia.Webpage.(*tg.WebPage)
			if !ok {
				return nil, fmt.Errorf("not a *tg.WebPage: %T", msgMedia.Webpage)
			}

			if pc, ok := webpage.GetPhoto(); ok && pc.TypeID() == tg.PhotoTypeID {
				log.Debug().
					Int64("photo_id", pc.GetID()).
					Msg("downloading webpage photo")
				readyTransferer = transferer.WithPhoto(pc)
			} else {
				return nil, fmt.Errorf("no photo found in webpage item")
			}
		default:
			return nil, fmt.Errorf("unhandled media type %T", msgMedia)
		}
	} else if info.PeerType == ids.PeerTypeUser {
		readyTransferer, err = transferer.WithUserPhoto(ctx, client.ScopedStore, info.PeerID, info.ID)
		if err != nil {
			return nil, fmt.Errorf("failed to create user photo transferer: %w", err)
		}
	} else if info.PeerType == ids.PeerTypeChat {
		readyTransferer = transferer.WithPeerPhoto(&tg.InputPeerChat{ChatID: info.PeerID}, info.ID)
	} else if info.PeerType == ids.PeerTypeChannel {
		readyTransferer, err = transferer.WithChannelPhoto(ctx, client.ScopedStore, info.PeerID, info.ID)
		if err != nil {
			return nil, err
		}
	} else if info.PeerType == ids.FakePeerTypeEmoji {
		customEmojiDocuments, err := client.client.API().MessagesGetCustomEmojiDocuments(ctx, []int64{info.ID})
		if err != nil {
			return nil, fmt.Errorf("failed to get custom emoji documents: %w", err)
		}
		if len(customEmojiDocuments) == 0 {
			return nil, fmt.Errorf("emoji id did not result in a document")
		}

		readyTransferer = media.NewTransferer(client.client.API()).
			WithStickerConfig(tc.Config.AnimatedSticker).
			WithDocument(customEmojiDocuments[0], false)
	}

	return readyTransferer.ToDirectMediaResponse(ctx)
}

func (tg *TelegramConnector) SetUseDirectMedia() {
	tg.useDirectMedia = true
}
