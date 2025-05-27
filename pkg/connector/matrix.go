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
	"crypto/sha256"
	"encoding/base64"
	"errors"
	"fmt"
	"image"
	_ "image/jpeg"
	_ "image/png"
	"math/rand/v2"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/gotd/td/telegram/message"
	"github.com/gotd/td/telegram/uploader"
	"github.com/gotd/td/tg"
	"github.com/rs/zerolog"
	"go.mau.fi/util/ffmpeg"
	"go.mau.fi/util/variationselector"
	"go.mau.fi/webp"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/event"
	"maunium.net/go/mautrix/id"

	"go.mau.fi/mautrix-telegram/pkg/connector/emojis"
	"go.mau.fi/mautrix-telegram/pkg/connector/humanise"
	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/connector/matrixfmt"
	"go.mau.fi/mautrix-telegram/pkg/connector/waveform"
)

func getMediaFilename(content *event.MessageEventContent) (filename string) {
	if content.FileName != "" {
		filename = content.FileName
	} else {
		filename = content.Body
	}
	if filename == "" {
		return "image.jpg" // Assume it's a JPEG image
	}
	if content.MsgType == event.MsgImage && (!strings.HasSuffix(filename, ".jpg") && !strings.HasSuffix(filename, ".jpeg") && !strings.HasSuffix(filename, ".png")) {
		if content.Info != nil && content.Info.MimeType != "" {
			return filename + "." + strings.TrimPrefix(content.Info.MimeType, "image/")
		}
		return filename + ".jpg" // Assume it's a JPEG
	}
	return filename
}

func (t *TelegramClient) transferMediaToTelegram(ctx context.Context, content *event.MessageEventContent, sticker bool) (tg.InputMediaClass, error) {
	var upload tg.InputFileClass
	var forceDocument bool
	filename := getMediaFilename(content)
	err := t.main.Bridge.Bot.DownloadMediaToFile(ctx, content.URL, content.File, false, func(f *os.File) (err error) {
		uploadFilename := f.Name()
		if sticker && content.Info != nil && (content.Info.MimeType == "image/png" || content.Info.MimeType == "image/jpeg") {
			tempFile, err := os.CreateTemp("", "telegram-sticker-*")
			if err != nil {
				return err
			}
			defer func() {
				tempFile.Close()
				os.Remove(tempFile.Name())
			}()
			if image, _, err := image.Decode(f); err != nil {
				return fmt.Errorf("failed to decode sticker image: %w", err)
			} else if err := webp.Encode(tempFile, image, nil); err != nil {
				return fmt.Errorf("failed to encode sticker webp image: %w", err)
			}
			uploadFilename = tempFile.Name()
			content.Info.MimeType = "image/webp"
		} else if sticker && content.Info != nil && (content.Info.MimeType != "video/webm" && content.Info.MimeType != "application/x-tgsticker") {
			uploadFilename, err = ffmpeg.ConvertPath(ctx, uploadFilename, ".webp", []string{}, []string{}, false)
			if err != nil {
				return fmt.Errorf("failed to convert sticker to webm: %+w", err)
			}
			defer os.Remove(uploadFilename)
			content.Info.MimeType = "image/webp"
		} else if cfg, _, err := image.DecodeConfig(f); err != nil {
			forceDocument = true
		} else if info, err := f.Stat(); err != nil {
			return err
		} else {
			// Telegram restricts photos in the following ways according to:
			// https://core.telegram.org/tdlib/docs/classtd_1_1td__api_1_1input_message_photo.html#ae1229ec5026a0b29dc398d87211bf572
			//
			// * The photo must be at most 10 MB in size.
			// * The photo's width and height must not exceed 10,000 in total
			// * Width and height ratio must be at most 20.
			//
			// We also have the image_as_file_pixels configuration threshold to
			// prevent Telegram from compressing the file.
			aspectRatio := float64(max(cfg.Height, cfg.Width)) / float64(min(cfg.Height, cfg.Width))
			forceDocument = cfg.Height*cfg.Width > t.main.Config.ImageAsFilePixels ||
				info.Size() > int64(10*1024*1024) ||
				aspectRatio > 20 ||
				cfg.Height+cfg.Width > 10000
		}

		uploader := uploader.NewUploader(t.client.API())
		upload, err = uploader.FromPath(ctx, uploadFilename, filename)
		return
	})
	if err != nil {
		return nil, fmt.Errorf("failed to download media from Matrix and upload media to Telegram: %w", err)
	}

	if !forceDocument && content.MsgType == event.MsgImage && content.Info != nil && (content.Info.MimeType == "image/jpeg" || content.Info.MimeType == "image/png") {
		return &tg.InputMediaUploadedPhoto{File: upload}, nil
	}

	var attributes []tg.DocumentAttributeClass
	attributes = append(attributes, &tg.DocumentAttributeFilename{FileName: filename})

	if content.Info != nil && content.Info.Width != 0 && content.Info.Height != 0 {
		attributes = append(attributes, &tg.DocumentAttributeImageSize{W: content.Info.Width, H: content.Info.Height})
	}

	if content.Info != nil && content.Info.MauGIF {
		attributes = append(attributes, &tg.DocumentAttributeAnimated{})
	}

	if sticker {
		attributes = append(attributes, &tg.DocumentAttributeSticker{
			Alt:        content.Body,
			Stickerset: &tg.InputStickerSetEmpty{},
		})
	} else if content.MsgType == event.MsgAudio {
		audioAttr := tg.DocumentAttributeAudio{
			Voice: content.MSC3245Voice != nil,
		}
		if content.MSC1767Audio != nil {
			audioAttr.Duration = content.MSC1767Audio.Duration / 1000
			if len(content.MSC1767Audio.Waveform) > 0 {
				audioAttr.Waveform = waveform.Encode(content.MSC1767Audio.Waveform)
			}
		}
		attributes = append(attributes, &audioAttr)
	}

	mimeType := "application/octet-stream"
	if content.Info != nil && content.Info.MimeType != "" {
		mimeType = content.Info.MimeType
	}
	return &tg.InputMediaUploadedDocument{
		File:       upload,
		MimeType:   mimeType,
		Attributes: attributes,
	}, nil
}

func (t *TelegramClient) humaniseSendError(err error) bridgev2.MessageStatus {
	status := bridgev2.WrapErrorInStatus(err).
		WithErrorReason(event.MessageStatusNetworkError).
		WithMessage(humanise.Error(err))

	switch {
	case tg.IsYouBlockedUser(err),
		tg.IsUserIsBlocked(err),
		tg.IsUserBlocked(err),
		tg.IsUserBannedInChannel(err),
		tg.IsChatAdminRequired(err),
		tg.IsChatRestricted(err),
		tg.IsChatWriteForbidden(err):
		status = status.WithErrorReason(event.MessageStatusNoPermission)
	case tg.IsMessageEmpty(err),
		tg.IsMessageTooLong(err),
		tg.IsEntitiesTooLong(err),
		tg.IsEntityBoundsInvalid(err),
		tg.IsEntityMentionUserInvalid(err):
		status = status.WithErrorReason(event.MessageStatusUnsupported)
	case tg.IsMessageEditTimeExpired(err):
		return status.WithErrorReason(event.MessageStatusUnsupported)
	case tg.IsMessageNotModified(err):
		status = status.WithErrorReason(event.MessageStatusNetworkError)
	default:
		// Return a normal status with the default retriable status
		return status
	}
	return status.WithIsCertain(true).
		WithStatus(event.MessageStatusFail)
}

func (tg *TelegramConnector) GenerateTransactionID(userID id.UserID, roomID id.RoomID, eventType event.Type) networkid.RawTransactionID {
	return networkid.RawTransactionID(strconv.FormatInt(rand.Int64(), 10))
}

func parseRandomID(txnID networkid.RawTransactionID) int64 {
	if txnID != "" {
		if id, err := strconv.ParseInt(string(txnID), 10, 64); err == nil && id > 0 {
			return id
		}
	}
	return rand.Int64()
}

func (t *TelegramClient) HandleMatrixMessage(ctx context.Context, msg *bridgev2.MatrixMessage) (resp *bridgev2.MatrixMessageResponse, err error) {
	// Handle Matrix events only after initial connection has been established to avoid deadlocking gotd
	select {
	case <-t.initialized:
	default:
		zerolog.Ctx(ctx).Warn().Msg("Got Matrix event before connected, blocking until done")

		select {
		case <-t.initialized:
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	}

	peer, err := t.inputPeerForPortalID(ctx, msg.Portal.ID)
	if err != nil {
		return nil, err
	}
	log := zerolog.Ctx(ctx).With().Stringer("portal_key", msg.Portal.PortalKey).Any("peer_id", peer).Logger()
	ctx = log.WithContext(ctx)

	var contentURI id.ContentURIString

	noWebpage := msg.Content.BeeperLinkPreviews != nil && len(msg.Content.BeeperLinkPreviews) == 0

	message, entities := matrixfmt.Parse(ctx, t.matrixParser, msg.Content)

	var replyTo tg.InputReplyToClass
	if msg.ReplyTo != nil {
		_, messageID, err := ids.ParseMessageID(msg.ReplyTo.ID)
		if err != nil {
			log.Warn().Msg("failed to parse replied-to message ID")
			return nil, err
		}
		replyTo = &tg.InputReplyToMessage{ReplyToMsgID: messageID}
	}

	randomID := parseRandomID(msg.InputTransactionID)

	var updates tg.UpdatesClass
	if msg.Event.Type == event.EventSticker {
		var media tg.InputMediaClass
		media, err = t.transferMediaToTelegram(ctx, msg.Content, true)
		if err != nil {
			return nil, err
		}
		updates, err = t.client.API().MessagesSendMedia(ctx, &tg.MessagesSendMediaRequest{
			Peer:     peer,
			Message:  message,
			Entities: entities,
			Media:    media,
			ReplyTo:  replyTo,
			RandomID: randomID,
		})
	} else {
		switch msg.Content.MsgType {
		case event.MsgText, event.MsgNotice, event.MsgEmote:
			updates, err = t.client.API().MessagesSendMessage(ctx, &tg.MessagesSendMessageRequest{
				Peer:      peer,
				NoWebpage: noWebpage,
				Message:   message,
				Entities:  entities,
				ReplyTo:   replyTo,
				RandomID:  randomID,
			})
		case event.MsgImage, event.MsgFile, event.MsgAudio, event.MsgVideo:
			var media tg.InputMediaClass
			media, err = t.transferMediaToTelegram(ctx, msg.Content, false)
			if err != nil {
				return nil, err
			}
			updates, err = t.client.API().MessagesSendMedia(ctx, &tg.MessagesSendMediaRequest{
				Peer:     peer,
				Message:  message,
				Entities: entities,
				Media:    media,
				ReplyTo:  replyTo,
				RandomID: randomID,
			})
		case event.MsgLocation:
			var uri GeoURI
			uri, err = ParseGeoURI(msg.Content.GeoURI)
			if err != nil {
				return nil, err
			}
			message = ""
			if location, ok := msg.Event.Content.Raw["org.matrix.msc3488.location"].(map[string]any); ok {
				if desc, ok := location["description"].(string); ok {
					message = desc
				}
			}
			updates, err = t.client.API().MessagesSendMedia(ctx, &tg.MessagesSendMediaRequest{
				Peer:    peer,
				Message: message,
				Media: &tg.InputMediaGeoPoint{
					GeoPoint: &tg.InputGeoPoint{Lat: uri.Lat, Long: uri.Long},
				},
				ReplyTo:  replyTo,
				RandomID: randomID,
			})
		default:
			return nil, fmt.Errorf("unsupported message type %s", msg.Content.MsgType)
		}
	}
	if err != nil {
		log.Err(err).Msg("failed to send message to Telegram")
		return nil, t.humaniseSendError(err)
	}

	hasher := sha256.New()

	var tgMessageID, tgDate int
	switch sentMessage := updates.(type) {
	case *tg.UpdateShortSentMessage:
		tgMessageID = sentMessage.ID
		tgDate = sentMessage.Date
		hasher.Write([]byte(msg.Content.Body))
	case *tg.Updates:
		tgDate = sentMessage.Date
		for _, u := range sentMessage.Updates {
			switch update := u.(type) {
			case *tg.UpdateMessageID:
				tgMessageID = update.ID
			case *tg.UpdateNewMessage:
				msg := update.Message.(*tg.Message)
				hasher.Write([]byte(msg.Message))
				hasher.Write(mediaHashID(ctx, msg.Media))
			}
		}
		if tgMessageID == 0 {
			return nil, fmt.Errorf("couldn't find update message ID update")
		}
	default:
		return nil, fmt.Errorf("unknown update from message response %T", updates)
	}

	messageID := ids.MakeMessageID(msg.Portal.PortalKey, tgMessageID)
	timestamp := time.Unix(int64(tgDate), 0)
	hash := hasher.Sum(nil)
	log.Info().
		Int("tg_message_id", tgMessageID).
		Str("message_id", string(messageID)).
		Time("timestamp", timestamp).
		Str("content_hash", base64.StdEncoding.EncodeToString(hash)).
		Msg("sent message successfully")

	resp = &bridgev2.MatrixMessageResponse{
		DB: &database.Message{
			ID:        messageID,
			MXID:      msg.Event.ID,
			Room:      msg.Portal.PortalKey,
			SenderID:  t.userID,
			Timestamp: timestamp,
			Metadata: &MessageMetadata{
				ContentHash: hash,
				ContentURI:  contentURI,
			},
		},
		StreamOrder: int64(tgMessageID),
	}
	return
}

func (t *TelegramClient) HandleMatrixEdit(ctx context.Context, msg *bridgev2.MatrixEdit) error {
	log := zerolog.Ctx(ctx).With().
		Str("conversion_direction", "to_telegram").
		Str("handler", "matrix_edit").
		Logger()

	peer, err := t.inputPeerForPortalID(ctx, msg.Portal.ID)
	if err != nil {
		return err
	}

	_, targetID, err := ids.ParseMessageID(msg.EditTarget.ID)
	if err != nil {
		return err
	}

	message, entities := matrixfmt.Parse(ctx, t.matrixParser, msg.Content)

	var newContentURI id.ContentURIString
	req := tg.MessagesEditMessageRequest{
		Peer:      peer,
		NoWebpage: msg.Content.BeeperLinkPreviews != nil && len(msg.Content.BeeperLinkPreviews) == 0,
		Message:   message,
		Entities:  entities,
		ID:        targetID,
	}
	if msg.Content.MsgType.IsMedia() {
		newContentURI = msg.Content.URL
		if newContentURI == "" {
			newContentURI = msg.Content.File.URL
		}
		if msg.EditTarget.Metadata.(*MessageMetadata).ContentURI == newContentURI {
			log.Info().Msg("media URI unchanged, skipping re-upload, just editing text")
		} else {
			log.Info().Msg("media URI changed, re-uploading media")
			req.Media, err = t.transferMediaToTelegram(ctx, msg.Content, false)
			if err != nil {
				return err
			}
		}
	} else if !msg.Content.MsgType.IsText() {
		return fmt.Errorf("editing message type %s is unsupported", msg.Content.MsgType)
	}
	updates, err := t.client.API().MessagesEditMessage(ctx, &req)
	if err != nil {
		return t.humaniseSendError(err)
	}

	hasher := sha256.New()

	switch sentMessage := updates.(type) {
	case *tg.UpdateShortSentMessage:
		hasher.Write([]byte(msg.Content.Body))
	case *tg.Updates:
		for _, u := range sentMessage.Updates {
			switch update := u.(type) {
			case *tg.UpdateNewMessage:
				msg := update.Message.(*tg.Message)
				hasher.Write([]byte(msg.Message))
				hasher.Write(mediaHashID(ctx, msg.Media))
			}
		}
	default:
		return fmt.Errorf("unknown update from message response %T", updates)
	}

	metadata := msg.EditTarget.Metadata.(*MessageMetadata)
	metadata.ContentHash = hasher.Sum(nil)
	metadata.ContentURI = newContentURI
	return nil
}

func (t *TelegramClient) HandleMatrixMessageRemove(ctx context.Context, msg *bridgev2.MatrixMessageRemove) error {
	if dbMsg, err := t.main.Bridge.DB.Message.GetPartByMXID(ctx, msg.TargetMessage.MXID); err != nil {
		return err
	} else if _, messageID, err := ids.ParseMessageID(dbMsg.ID); err != nil {
		return err
	} else if peer, err := t.inputPeerForPortalID(ctx, msg.Portal.ID); err != nil {
		return err
	} else {
		_, err := message.NewSender(t.client.API()).
			To(peer).
			Revoke().
			Messages(ctx, messageID)
		return err
	}
}

func (t *TelegramClient) PreHandleMatrixReaction(ctx context.Context, msg *bridgev2.MatrixReaction) (bridgev2.MatrixReactionPreResponse, error) {
	log := zerolog.Ctx(ctx).With().
		Str("conversion_direction", "to_telegram").
		Str("handler", "pre_handle_matrix_reaction").
		Str("key", msg.Content.RelatesTo.Key).
		Logger()
	var resp bridgev2.MatrixReactionPreResponse

	var maxReactions int
	maxReactions, err := t.getReactionLimit(ctx, t.userID)
	if err != nil {
		return resp, err
	}

	keyNoVariation := variationselector.Remove(msg.Content.RelatesTo.Key)
	emojiID := ids.MakeEmojiIDFromEmoticon(msg.Content.RelatesTo.Key)
	if strings.HasPrefix(msg.Content.RelatesTo.Key, "mxc://") {
		if file, err := t.main.Store.TelegramFile.GetByMXC(ctx, msg.Content.RelatesTo.Key); err != nil {
			return resp, err
		} else if file == nil {
			return resp, fmt.Errorf("reaction MXC URI %s does not correspond with any known Telegram files", msg.Content.RelatesTo.Key)
		} else if documentID, err := strconv.ParseInt(string(file.LocationID), 10, 64); err != nil {
			return resp, err
		} else {
			emojiID = ids.MakeEmojiIDFromDocumentID(documentID)
		}
	} else if t.main.Config.AlwaysCustomEmojiReaction {
		// Always use the unicodemoji reaction if available
		if documentID, ok := emojis.GetEmojiDocumentID(keyNoVariation); ok {
			log.Debug().Msg("Using custom emoji reaction")
			emojiID = ids.MakeEmojiIDFromDocumentID(documentID)
		}
	} else if availableReactions, err := t.getAvailableReactions(ctx); err != nil {
		return resp, fmt.Errorf("failed to get available reactions: %w", err)
	} else if _, ok := availableReactions[keyNoVariation]; ok {
		log.Debug().Msg("Not using custom emoji reaction since the emoji is available")
	} else {
		if documentID, ok := emojis.GetEmojiDocumentID(keyNoVariation); ok {
			log.Debug().Msg("Using custom emoji reaction")
			emojiID = ids.MakeEmojiIDFromDocumentID(documentID)
		}
	}

	log.Debug().Str("emoji_id", string(emojiID)).Msg("Pre-handled reaction")

	return bridgev2.MatrixReactionPreResponse{
		SenderID:     t.userID,
		EmojiID:      emojiID,
		Emoji:        variationselector.FullyQualify(msg.Content.RelatesTo.Key),
		MaxReactions: maxReactions,
	}, nil
}

func (t *TelegramClient) appendEmojiID(reactionList []tg.ReactionClass, emojiID networkid.EmojiID) ([]tg.ReactionClass, error) {
	if documentID, emoticon, err := ids.ParseEmojiID(emojiID); err != nil {
		return nil, err
	} else if documentID > 0 {
		return append(reactionList, &tg.ReactionCustomEmoji{DocumentID: documentID}), nil
	} else {
		return append(reactionList, &tg.ReactionEmoji{Emoticon: emoticon}), nil
	}
}

func (t *TelegramClient) HandleMatrixReaction(ctx context.Context, msg *bridgev2.MatrixReaction) (reaction *database.Reaction, err error) {
	peer, err := t.inputPeerForPortalID(ctx, msg.Portal.ID)
	if err != nil {
		return nil, err
	}
	_, targetMessageID, err := ids.ParseMessageID(msg.TargetMessage.ID)
	if err != nil {
		return nil, err
	}

	var newReactions []tg.ReactionClass
	for _, existing := range msg.ExistingReactionsToKeep {
		newReactions, err = t.appendEmojiID(newReactions, existing.EmojiID)
		if err != nil {
			return nil, err
		}
	}
	newReactions, err = t.appendEmojiID(newReactions, msg.PreHandleResp.EmojiID)
	if err != nil {
		return nil, err
	}

	_, err = t.client.API().MessagesSendReaction(ctx, &tg.MessagesSendReactionRequest{
		Peer:        peer,
		AddToRecent: true,
		MsgID:       targetMessageID,
		Reaction:    newReactions,
	})
	if tg.IsReactionInvalid(err) {
		return nil, bridgev2.WrapErrorInStatus(err).
			WithErrorReason(event.MessageStatusUnsupported).
			WithIsCertain(true).
			WithMessage("Unsupported reaction")
	}
	return &database.Reaction{}, err
}

func (t *TelegramClient) HandleMatrixReactionRemove(ctx context.Context, msg *bridgev2.MatrixReactionRemove) error {
	peer, err := t.inputPeerForPortalID(ctx, msg.Portal.ID)
	if err != nil {
		return err
	}

	var newReactions []tg.ReactionClass

	if maxReactions, err := t.getReactionLimit(ctx, t.userID); err != nil {
		return err
	} else if maxReactions > 1 {
		existing, err := t.main.Bridge.DB.Reaction.GetAllToMessageBySender(ctx, msg.Portal.Receiver, msg.TargetReaction.MessageID, msg.TargetReaction.SenderID)
		if err != nil {
			return err
		}
		for _, existing := range existing {
			if msg.TargetReaction.EmojiID != existing.EmojiID {
				newReactions, err = t.appendEmojiID(newReactions, existing.EmojiID)
				if err != nil {
					return err
				}
			}
		}
	}

	_, messageID, err := ids.ParseMessageID(msg.TargetReaction.MessageID)
	if err != nil {
		return err
	}
	_, err = t.client.API().MessagesSendReaction(ctx, &tg.MessagesSendReactionRequest{
		Peer:        peer,
		AddToRecent: true,
		MsgID:       messageID,
		Reaction:    newReactions,
	})
	return err
}

func (t *TelegramClient) HandleMatrixReadReceipt(ctx context.Context, msg *bridgev2.MatrixReadReceipt) error {
	log := zerolog.Ctx(ctx).With().
		Str("action", "handle_matrix_read_receipt").
		Str("portal_id", string(msg.Portal.ID)).
		Bool("is_supergroup", msg.Portal.Metadata.(*PortalMetadata).IsSuperGroup).
		Logger()
	peerType, portalID, parseErr := ids.ParsePortalID(msg.Portal.ID)
	if parseErr != nil {
		return parseErr
	}
	inputPeer, parseErr := t.inputPeerForPortalID(ctx, msg.Portal.ID)
	if parseErr != nil {
		return parseErr
	}

	var readMentionsErr, readReactionsErr, readMessagesErr, reactionPollErr error
	var wg sync.WaitGroup

	// Read mentions
	wg.Add(1)
	go func() {
		defer wg.Done()
		_, readMentionsErr = t.client.API().MessagesReadMentions(ctx, &tg.MessagesReadMentionsRequest{
			Peer: inputPeer,
		})
	}()

	// Read reactions
	wg.Add(1)
	go func() {
		defer wg.Done()
		_, readMentionsErr = t.client.API().MessagesReadReactions(ctx, &tg.MessagesReadReactionsRequest{
			Peer: inputPeer,
		})
	}()

	// Read messages
	wg.Add(1)
	go func() {
		defer wg.Done()

		message := msg.ExactMessage
		if message == nil {
			message, readMessagesErr = t.main.Bridge.DB.Message.GetLastPartAtOrBeforeTime(ctx, msg.Portal.PortalKey, time.Now())
			if readMessagesErr != nil {
				return
			} else if message == nil {
				zerolog.Ctx(ctx).Warn().Msg("no message found to read")
				return
			}
		}
		var maxID int
		_, maxID, readMessagesErr = ids.ParseMessageID(message.ID)
		if readMessagesErr != nil {
			return
		}

		switch peerType {
		case ids.PeerTypeUser, ids.PeerTypeChat:
			_, readMessagesErr = t.client.API().MessagesReadHistory(ctx, &tg.MessagesReadHistoryRequest{
				Peer:  inputPeer,
				MaxID: maxID,
			})
		case ids.PeerTypeChannel:
			var accessHash int64
			accessHash, readMessagesErr = t.ScopedStore.GetAccessHash(ctx, ids.PeerTypeChannel, portalID)
			if readMessagesErr != nil {
				return
			}
			_, readMessagesErr = t.client.API().ChannelsReadHistory(ctx, &tg.ChannelsReadHistoryRequest{
				Channel: &tg.InputChannel{ChannelID: portalID, AccessHash: accessHash},
			})

			if !msg.Portal.Metadata.(*PortalMetadata).IsSuperGroup {
				// TODO handle sponsored message read receipts
			}
		default:
			readMessagesErr = fmt.Errorf("unknown peer type %s", peerType)
		}
	}()

	// Poll for reactions
	wg.Add(1)
	go func() {
		defer wg.Done()
		if peerType != ids.PeerTypeChannel || msg.Portal.Metadata.(*PortalMetadata).IsSuperGroup {
			log.Debug().Msg("Not polling reactions because peer is not a channel or is a super-group")
			return
		}

		// If it hasn't been 20 seconds since the last poll, skip
		now := time.Now()
		if prev, ok := t.prevReactionPoll[msg.Portal.PortalKey]; ok && now.Before(prev.Add(20*time.Second)) {
			log.Debug().Msg("Not polling reactions because last poll was less than 20 seconds ago")
			return
		}
		t.prevReactionPoll[msg.Portal.PortalKey] = now

		reactionPollErr = t.pollForReactions(ctx, msg.Portal.PortalKey, inputPeer)
	}()

	wg.Wait()
	return errors.Join(readMentionsErr, readReactionsErr, readMessagesErr, reactionPollErr)
}

func (t *TelegramClient) HandleMatrixTyping(ctx context.Context, msg *bridgev2.MatrixTyping) error {
	inputPeer, err := t.inputPeerForPortalID(ctx, msg.Portal.ID)
	if err != nil {
		return err
	}
	_, err = t.client.API().MessagesSetTyping(ctx, &tg.MessagesSetTypingRequest{
		Peer:   inputPeer,
		Action: &tg.SendMessageTypingAction{},
	})
	return err
}
