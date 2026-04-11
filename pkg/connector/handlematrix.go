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
	"bytes"
	"cmp"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"errors"
	"fmt"
	"image"
	"image/jpeg"
	_ "image/jpeg"
	_ "image/png"
	"io"
	"math"
	"math/rand/v2"
	"os"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/rs/zerolog"
	"go.mau.fi/util/exsync"
	"go.mau.fi/util/ffmpeg"
	"go.mau.fi/util/jsontime"
	"go.mau.fi/util/variationselector"
	"go.mau.fi/webp"
	"golang.org/x/exp/maps"
	_ "golang.org/x/image/webp"
	"golang.org/x/net/html"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/bridgev2/simplevent"
	"maunium.net/go/mautrix/event"
	"maunium.net/go/mautrix/id"

	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/message"
	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/uploader"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"

	"go.mau.fi/mautrix-telegram/pkg/connector/emojis"
	"go.mau.fi/mautrix-telegram/pkg/connector/humanise"
	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/connector/matrixfmt"
	"go.mau.fi/mautrix-telegram/pkg/connector/waveform"
)

var (
	_ bridgev2.EditHandlingNetworkAPI           = (*TelegramClient)(nil)
	_ bridgev2.ReactionHandlingNetworkAPI       = (*TelegramClient)(nil)
	_ bridgev2.RedactionHandlingNetworkAPI      = (*TelegramClient)(nil)
	_ bridgev2.ReadReceiptHandlingNetworkAPI    = (*TelegramClient)(nil)
	_ bridgev2.TypingHandlingNetworkAPI         = (*TelegramClient)(nil)
	_ bridgev2.DisappearTimerChangingNetworkAPI = (*TelegramClient)(nil)
	_ bridgev2.MuteHandlingNetworkAPI           = (*TelegramClient)(nil)
	_ bridgev2.TagHandlingNetworkAPI            = (*TelegramClient)(nil)
	_ bridgev2.ChatViewingNetworkAPI            = (*TelegramClient)(nil)
	_ bridgev2.DeleteChatHandlingNetworkAPI     = (*TelegramClient)(nil)
	_ bridgev2.RoomNameHandlingNetworkAPI       = (*TelegramClient)(nil)
	_ bridgev2.RoomAvatarHandlingNetworkAPI     = (*TelegramClient)(nil)
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

func (tc *TelegramClient) HandleMatrixViewingChat(ctx context.Context, msg *bridgev2.MatrixViewingChat) error {
	if msg.Portal == nil {
		return nil
	}
	_, _, topicID, _ := ids.ParsePortalID(msg.Portal.PortalKey.ID)
	// TODO sync topic parent space
	meta := msg.Portal.Metadata.(*PortalMetadata)
	if (topicID == 0 && !meta.FullSynced) || meta.LastSync.Add(24*time.Hour).Before(time.Now()) {
		tc.userLogin.QueueRemoteEvent(&simplevent.ChatResync{
			EventMeta: simplevent.EventMeta{
				Type:      bridgev2.RemoteEventChatResync,
				PortalKey: msg.Portal.PortalKey,
			},
			GetChatInfoFunc: tc.GetChatInfo,
		})
	}
	err := tc.maybePollForReactions(ctx, msg.Portal)
	if err != nil {
		return err
	}
	err = tc.pollSponsoredMessage(ctx, msg.Portal)
	if err != nil {
		return err
	}
	return nil
}

func (tc *TelegramClient) pollSponsoredMessage(ctx context.Context, portal *bridgev2.Portal) error {
	if tc.metadata.IsBot {
		return nil
	}
	meta := portal.Metadata.(*PortalMetadata)
	peerType, id, topicID, err := ids.ParsePortalID(portal.ID)
	if err != nil {
		return err
	} else if peerType != ids.PeerTypeChannel || meta.IsSuperGroup || topicID != 0 {
		return nil
	}
	meta.sponsoredMessageLock.Lock()
	defer meta.sponsoredMessageLock.Unlock()
	if time.Since(meta.SponsoredMessagePollTS.Time) < 5*time.Minute {
		return nil
	}
	latestMessage, err := tc.main.Bridge.DB.Message.GetLastNonFakePartAtOrBeforeTime(ctx, portal.PortalKey, time.Now())
	if err != nil {
		return fmt.Errorf("failed to get latest message for portal: %w", err)
	} else if latestMessage != nil && latestMessage.ID == meta.LastMessageOnSponsorFetch {
		meta.SponsoredMessagePollTS = jsontime.UnixNow()
		return nil
	}
	accessHash, err := tc.ScopedStore.GetAccessHash(ctx, ids.PeerTypeChannel, id)
	if err != nil {
		return err
	}
	resp, err := tc.client.API().MessagesGetSponsoredMessages(ctx, &tg.MessagesGetSponsoredMessagesRequest{
		Peer: &tg.InputPeerChannel{ChannelID: id, AccessHash: accessHash},
	})
	if err != nil {
		return fmt.Errorf("failed to get sponsored messages: %w", err)
	}
	meta.SponsoredMessagePollTS = jsontime.UnixNow()
	if latestMessage != nil {
		meta.LastMessageOnSponsorFetch = latestMessage.ID
	}
	msgs, ok := resp.(*tg.MessagesSponsoredMessages)
	if !ok || len(msgs.Messages) == 0 || (len(msgs.Messages) == 1 && bytes.Equal(msgs.Messages[0].RandomID, meta.SponsoredMessageRandomID)) {
		err = portal.Save(ctx)
		if err != nil {
			return fmt.Errorf("failed to save portal after polling sponsored messages: %w", err)
		}
		return nil
	}
	if meta.sponsoredMessageSeen == nil {
		meta.sponsoredMessageSeen = exsync.NewSet[int64]()
	} else {
		meta.sponsoredMessageSeen.Clear()
	}
	msg := msgs.Messages[0]
	if bytes.Equal(msg.RandomID, meta.SponsoredMessageRandomID) && len(msgs.Messages) > 1 {
		msg = msgs.Messages[1]
	}
	meta.SponsoredMessageRandomID = msg.RandomID
	content := tc.parseBodyAndHTML(ctx, msg.Message, msg.Entities)
	content.MsgType = event.MsgNotice
	content.EnsureHasHTML()
	extra := map[string]any{
		"external_url": msg.URL,
		"fi.mau.telegram.sponsored": map[string]any{
			"random_id":       msg.RandomID,
			"url":             msg.URL,
			"button_text":     msg.ButtonText,
			"title":           msg.Title,
			"content":         content.FormattedBody,
			"sponsor_info":    msg.SponsorInfo,
			"additional_info": msg.AdditionalInfo,
			"recommended":     msg.Recommended,
		},
	}
	var fromStr string
	if msg.SponsorInfo != "" {
		fromStr = fmt.Sprintf(" from %s", html.EscapeString(msg.SponsorInfo))
	}
	prefix := "Ad"
	if msg.Recommended {
		prefix = "Recommended"
	}
	content.FormattedBody = fmt.Sprintf(
		`<strong>%s: %s</strong><blockquote>%s</blockquote><p>Sponsored message%s - <a href="%s">%s</a></p>`,
		prefix, html.EscapeString(msg.Title), content.FormattedBody, fromStr, msg.URL, msg.ButtonText,
	)
	sendResp, err := tc.main.Bridge.Bot.SendMessage(ctx, portal.MXID, event.EventMessage, &event.Content{
		Raw:    extra,
		Parsed: content,
	}, &bridgev2.MatrixSendExtra{Timestamp: time.Now()})
	if err != nil {
		return fmt.Errorf("failed to send sponsored message: %w", err)
	}
	meta.SponsoredMessageEventID = sendResp.EventID
	zerolog.Ctx(ctx).Debug().
		Stringer("event_id", sendResp.EventID).
		Str("random_id", base64.StdEncoding.EncodeToString(msg.RandomID)).
		Msg("Sent sponsored message to Matrix")
	err = portal.Save(ctx)
	if err != nil {
		return fmt.Errorf("failed to save portal after sending sponsored messages: %w", err)
	}
	return nil
}

func (tc *TelegramClient) transferMediaToTelegram(ctx context.Context, content *event.MessageEventContent, sticker bool) (tg.InputMediaClass, error) {
	var upload tg.InputFileClass
	var forceDocument bool
	filename := getMediaFilename(content)
	info := content.GetInfo()
	err := tc.main.Bridge.Bot.DownloadMediaToFile(ctx, content.URL, content.File, false, func(f *os.File) (err error) {
		uploadFilename := f.Name()
		if sticker && (info.MimeType == "image/png" || info.MimeType == "image/jpeg") {
			tempFile, err := os.CreateTemp("", "telegram-sticker-*.webp")
			if err != nil {
				return err
			}
			defer func() {
				_ = tempFile.Close()
				_ = os.Remove(tempFile.Name())
			}()
			if img, _, err := image.Decode(f); err != nil {
				return fmt.Errorf("failed to decode sticker image: %w", err)
			} else if err := webp.Encode(tempFile, img, nil); err != nil {
				return fmt.Errorf("failed to encode sticker webp image: %w", err)
			}
			uploadFilename = tempFile.Name()
			info.MimeType = "image/webp"
		} else if sticker && (info.MimeType != "video/webm" && info.MimeType != "application/x-tgsticker") {
			uploadFilename, err = ffmpeg.ConvertPath(ctx, uploadFilename, ".webp", []string{}, []string{}, false)
			if err != nil {
				return fmt.Errorf("failed to convert sticker to webm: %+w", err)
			}
			defer os.Remove(uploadFilename)
			info.MimeType = "image/webp"
		} else if cfg, _, err := image.DecodeConfig(f); err != nil {
			forceDocument = true
		} else if fileInfo, err := f.Stat(); err != nil {
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
			forceDocument = cfg.Height*cfg.Width > tc.main.Config.ImageAsFilePixels ||
				fileInfo.Size() > int64(10*1024*1024) ||
				aspectRatio > 20 ||
				cfg.Height+cfg.Width > 10000
		}
		if !forceDocument && !sticker && content.MsgType == event.MsgImage {
			_, err = f.Seek(0, io.SeekStart)
			if err != nil {
				return err
			}
			tempFile, err := os.CreateTemp("", "telegram-nonsticker-*.jpeg")
			if err != nil {
				return err
			}
			defer func() {
				_ = tempFile.Close()
				_ = os.Remove(tempFile.Name())
			}()
			if img, _, err := image.Decode(f); err != nil {
				return fmt.Errorf("failed to decode non-sticker webp image: %w", err)
			} else if err := jpeg.Encode(tempFile, img, nil); err != nil {
				return fmt.Errorf("failed to encode non-sticker jpeg image: %w", err)
			}
			uploadFilename = tempFile.Name()
			filename += ".jpeg"
			info.MimeType = "image/jpeg"
		}

		upload, err = uploader.NewUploader(tc.client.API()).FromPath(ctx, uploadFilename, filename)
		return
	})
	if err != nil {
		return nil, fmt.Errorf("failed to download media from Matrix and upload media to Telegram: %w", err)
	}

	if !forceDocument && content.MsgType == event.MsgImage && (info.MimeType == "image/jpeg" || info.MimeType == "image/png") {
		return &tg.InputMediaUploadedPhoto{File: upload}, nil
	}

	var attributes []tg.DocumentAttributeClass
	attributes = append(attributes, &tg.DocumentAttributeFilename{FileName: filename})

	if info.Width != 0 && info.Height != 0 && content.MsgType == event.MsgImage {
		attributes = append(attributes, &tg.DocumentAttributeImageSize{W: info.Width, H: info.Height})
	}

	if info.MauGIF {
		attributes = append(attributes, &tg.DocumentAttributeAnimated{})
	}

	if sticker {
		attributes = append(attributes, &tg.DocumentAttributeSticker{
			Alt:        content.Body,
			Stickerset: &tg.InputStickerSetEmpty{},
		})
	} else if content.MsgType == event.MsgAudio {
		audioAttr := &tg.DocumentAttributeAudio{
			Voice:    content.MSC3245Voice != nil,
			Duration: info.Duration / 1000,
		}
		if content.MSC1767Audio != nil && len(content.MSC1767Audio.Waveform) > 0 {
			audioAttr.Waveform = waveform.Encode(content.MSC1767Audio.Waveform)
		}
		attributes = append(attributes, audioAttr)
	} else if content.MsgType == event.MsgVideo {
		attributes = append(attributes, &tg.DocumentAttributeVideo{
			Duration: float64(info.Duration) / 1000,
			W:        info.Width,
			H:        info.Height,
		})
	}

	return &tg.InputMediaUploadedDocument{
		File:       upload,
		MimeType:   cmp.Or(info.MimeType, "application/octet-stream"),
		Attributes: attributes,
	}, nil
}

func (tc *TelegramClient) humaniseSendError(err error) bridgev2.MessageStatus {
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

func (tc *TelegramConnector) GenerateTransactionID(userID id.UserID, roomID id.RoomID, eventType event.Type) networkid.RawTransactionID {
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

func (tc *TelegramClient) HandleMatrixMessage(ctx context.Context, msg *bridgev2.MatrixMessage) (resp *bridgev2.MatrixMessageResponse, err error) {
	if msg.Portal.RoomType == database.RoomTypeSpace {
		return nil, fmt.Errorf("can't send messages to space portals")
	}
	// Handle Matrix events only after initial connection has been established to avoid deadlocking gotd
	err = tc.clientInitialized.Wait(ctx)
	if err != nil {
		return nil, err
	}

	peer, topicID, err := tc.inputPeerForPortalID(ctx, msg.Portal.ID)
	if err != nil {
		return nil, err
	}
	log := zerolog.Ctx(ctx).With().
		Stringer("portal_key", msg.Portal.PortalKey).
		Any("peer_id", peer).
		Logger()
	ctx = log.WithContext(ctx)

	var contentURI id.ContentURIString

	noWebpage := msg.Content.BeeperLinkPreviews != nil && len(msg.Content.BeeperLinkPreviews) == 0

	message, entities := matrixfmt.Parse(ctx, tc.matrixParser, msg.Content, msg.Portal)

	var replyTo tg.InputReplyToClass
	if msg.ReplyTo != nil {
		_, messageID, err := ids.ParseMessageID(msg.ReplyTo.ID)
		if err != nil {
			log.Warn().Msg("failed to parse replied-to message ID")
			return nil, err
		}
		replyTo = &tg.InputReplyToMessage{ReplyToMsgID: messageID}
	}
	if topicID > 0 {
		if replyTo == nil {
			replyTo = &tg.InputReplyToMessage{ReplyToMsgID: topicID}
		} else {
			replyTo.(*tg.InputReplyToMessage).TopMsgID = topicID
		}
	}

	randomID := parseRandomID(msg.InputTransactionID)

	var updates tg.UpdatesClass
	if msg.Event.Type == event.EventSticker {
		var media tg.InputMediaClass
		media, err = tc.transferMediaToTelegram(ctx, msg.Content, true)
		if err != nil {
			return nil, err
		}
		updates, err = tc.client.API().MessagesSendMedia(ctx, &tg.MessagesSendMediaRequest{
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
			updates, err = tc.client.API().MessagesSendMessage(ctx, &tg.MessagesSendMessageRequest{
				Peer:      peer,
				NoWebpage: noWebpage,
				Message:   message,
				Entities:  entities,
				ReplyTo:   replyTo,
				RandomID:  randomID,
			})
		case event.MsgImage, event.MsgFile, event.MsgAudio, event.MsgVideo:
			var media tg.InputMediaClass
			media, err = tc.transferMediaToTelegram(ctx, msg.Content, false)
			if err != nil {
				return nil, err
			}
			updates, err = tc.client.API().MessagesSendMedia(ctx, &tg.MessagesSendMediaRequest{
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
			updates, err = tc.client.API().MessagesSendMedia(ctx, &tg.MessagesSendMediaRequest{
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
		return nil, tc.humaniseSendError(err)
	}

	hasher := sha256.New()

	var tgMessageID, tgDate int
	switch sentMessage := updates.(type) {
	case *tg.UpdateShortSentMessage:
		tgMessageID = sentMessage.ID
		tgDate = sentMessage.Date
		hasher.Write([]byte(msg.Content.Body))
		hasher.Write(mediaHashID(ctx, sentMessage.Media))
	case *tg.Updates:
		var realSentMessage *tg.Message
		for _, u := range sentMessage.Updates {
			switch update := u.(type) {
			case *tg.UpdateMessageID:
				tgMessageID = update.ID
				if update.RandomID != randomID {
					log.Warn().
						Int64("update_random_id", update.RandomID).
						Int64("expected_random_id", randomID).
						Msg("Random ID in response does not match sent random ID")
				}
			case *tg.UpdateNewMessage:
				if realSentMessage != nil {
					log.Warn().
						Int("prev_id", realSentMessage.ID).
						Int("new_id", update.Message.GetID()).
						Msg("Multiple messages in send response")
				}
				realSentMessage = update.Message.(*tg.Message)
			case *tg.UpdateNewChannelMessage:
				if realSentMessage != nil {
					log.Warn().
						Int("prev_id", realSentMessage.ID).
						Int("new_id", update.Message.GetID()).
						Msg("Multiple messages in send response")
				}
				realSentMessage = update.Message.(*tg.Message)
			case *tg.UpdateReadChannelInbox, *tg.UpdateReadHistoryInbox, *tg.UpdateReadMonoForumInbox,
				*tg.UpdateReadHistoryOutbox, *tg.UpdateReadChannelOutbox:
				// ignore
			default:
				log.Warn().Type("update_type", update).Msg("Unexpected update type in send message response")
			}
		}
		if realSentMessage != nil {
			tgDate = realSentMessage.Date
			hasher.Write([]byte(realSentMessage.Message))
			hasher.Write(mediaHashID(ctx, realSentMessage.Media))
		} else {
			hasher.Write([]byte(msg.Content.Body))
			tgDate = sentMessage.Date
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
			SenderID:  tc.userID,
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

func (tc *TelegramClient) HandleMatrixEdit(ctx context.Context, msg *bridgev2.MatrixEdit) error {
	if msg.Portal.RoomType == database.RoomTypeSpace {
		return fmt.Errorf("can't send messages to space portals")
	}
	log := zerolog.Ctx(ctx).With().
		Str("conversion_direction", "to_telegram").
		Str("handler", "matrix_edit").
		Logger()

	peer, _, err := tc.inputPeerForPortalID(ctx, msg.Portal.ID)
	if err != nil {
		return err
	}

	_, targetID, err := ids.ParseMessageID(msg.EditTarget.ID)
	if err != nil {
		return err
	}

	message, entities := matrixfmt.Parse(ctx, tc.matrixParser, msg.Content, msg.Portal)

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
			req.Media, err = tc.transferMediaToTelegram(ctx, msg.Content, false)
			if err != nil {
				return err
			}
		}
	} else if !msg.Content.MsgType.IsText() {
		return fmt.Errorf("editing message type %s is unsupported", msg.Content.MsgType)
	}
	updates, err := tc.client.API().MessagesEditMessage(ctx, &req)
	if err != nil {
		return tc.humaniseSendError(err)
	}

	hasher := sha256.New()

	switch sentMessage := updates.(type) {
	case *tg.UpdateShortSentMessage:
		hasher.Write([]byte(msg.Content.Body))
		hasher.Write(mediaHashID(ctx, sentMessage.Media))
	case *tg.Updates:
		var realSentMessage *tg.Message
		for _, u := range sentMessage.Updates {
			switch update := u.(type) {
			case *tg.UpdateNewMessage:
				if realSentMessage != nil {
					log.Warn().
						Int("prev_id", realSentMessage.ID).
						Int("new_id", update.Message.GetID()).
						Msg("Multiple messages in edit response")
				}
				realSentMessage = update.Message.(*tg.Message)
			case *tg.UpdateNewChannelMessage:
				if realSentMessage != nil {
					log.Warn().
						Int("prev_id", realSentMessage.ID).
						Int("new_id", update.Message.GetID()).
						Msg("Multiple messages in edit response")
				}
				realSentMessage = update.Message.(*tg.Message)
			default:
				log.Warn().Type("update_type", update).Msg("Unexpected update type in edit message response")
			}
		}
		if realSentMessage != nil {
			hasher.Write([]byte(realSentMessage.Message))
			hasher.Write(mediaHashID(ctx, realSentMessage.Media))
		} else {
			hasher.Write([]byte(msg.Content.Body))
		}
	default:
		return fmt.Errorf("unknown update from message response %T", updates)
	}

	metadata := msg.EditTarget.Metadata.(*MessageMetadata)
	metadata.ContentHash = hasher.Sum(nil)
	metadata.ContentURI = newContentURI
	return nil
}

func (tc *TelegramClient) HandleMatrixMessageRemove(ctx context.Context, msg *bridgev2.MatrixMessageRemove) error {
	if msg.Portal.RoomType == database.RoomTypeSpace {
		return fmt.Errorf("can't send messages to space portals")
	} else if dbMsg, err := tc.main.Bridge.DB.Message.GetPartByMXID(ctx, msg.TargetMessage.MXID); err != nil {
		return err
	} else if _, messageID, err := ids.ParseMessageID(dbMsg.ID); err != nil {
		return err
	} else if peer, _, err := tc.inputPeerForPortalID(ctx, msg.Portal.ID); err != nil {
		return err
	} else {
		_, err := message.NewSender(tc.client.API()).
			To(peer).
			Revoke().
			Messages(ctx, messageID)
		return err
	}
}

func (tc *TelegramClient) PreHandleMatrixReaction(ctx context.Context, msg *bridgev2.MatrixReaction) (bridgev2.MatrixReactionPreResponse, error) {
	if msg.Portal.RoomType == database.RoomTypeSpace {
		return bridgev2.MatrixReactionPreResponse{}, fmt.Errorf("can't send messages to space portals")
	}
	log := zerolog.Ctx(ctx).With().
		Str("conversion_direction", "to_telegram").
		Str("handler", "pre_handle_matrix_reaction").
		Str("key", msg.Content.RelatesTo.Key).
		Logger()
	var resp bridgev2.MatrixReactionPreResponse

	var maxReactions int
	maxReactions, err := tc.getReactionLimit(ctx, tc.userID)
	if err != nil {
		return resp, err
	}

	keyNoVariation := variationselector.Remove(msg.Content.RelatesTo.Key)
	emojiID := ids.MakeEmojiIDFromEmoticon(msg.Content.RelatesTo.Key)
	if strings.HasPrefix(msg.Content.RelatesTo.Key, "mxc://") {
		if file, err := tc.main.Store.TelegramFile.GetByMXC(ctx, id.ContentURIString(msg.Content.RelatesTo.Key)); err != nil {
			return resp, err
		} else if file == nil {
			return resp, fmt.Errorf("reaction MXC URI %s does not correspond with any known Telegram files", msg.Content.RelatesTo.Key)
		} else if documentID, err := strconv.ParseInt(string(file.LocationID), 10, 64); err != nil {
			return resp, err
		} else {
			emojiID = ids.MakeEmojiIDFromDocumentID(documentID)
		}
	} else if tc.main.Config.AlwaysCustomEmojiReaction {
		// Always use the unicodemoji reaction if available
		if documentID, ok := emojis.GetEmojiDocumentID(keyNoVariation); ok {
			log.Debug().Msg("Using custom emoji reaction")
			emojiID = ids.MakeEmojiIDFromDocumentID(documentID)
		}
	} else if availableReactions, err := tc.getAvailableReactions(ctx); err != nil {
		return resp, fmt.Errorf("failed to get available reactions: %w", err)
	} else if _, ok := availableReactions[keyNoVariation]; ok {
		log.Debug().Msg("Not using custom emoji reaction since the emoji is available")
	} else if documentID, ok := emojis.GetEmojiDocumentID(keyNoVariation); ok && !tc.metadata.IsBot {
		log.Debug().Msg("Using custom emoji reaction")
		emojiID = ids.MakeEmojiIDFromDocumentID(documentID)
	}

	log.Debug().Str("emoji_id", string(emojiID)).Msg("Pre-handled reaction")

	return bridgev2.MatrixReactionPreResponse{
		SenderID:     tc.userID,
		EmojiID:      emojiID,
		Emoji:        variationselector.FullyQualify(msg.Content.RelatesTo.Key),
		MaxReactions: maxReactions,
	}, nil
}

func (tc *TelegramClient) appendEmojiID(reactionList []tg.ReactionClass, emojiID networkid.EmojiID) ([]tg.ReactionClass, error) {
	if documentID, emoticon, err := ids.ParseEmojiID(emojiID); err != nil {
		return nil, err
	} else if documentID > 0 {
		return append(reactionList, &tg.ReactionCustomEmoji{DocumentID: documentID}), nil
	} else {
		return append(reactionList, &tg.ReactionEmoji{Emoticon: emoticon}), nil
	}
}

func (tc *TelegramClient) HandleMatrixReaction(ctx context.Context, msg *bridgev2.MatrixReaction) (reaction *database.Reaction, err error) {
	peer, _, err := tc.inputPeerForPortalID(ctx, msg.Portal.ID)
	if err != nil {
		return nil, err
	}
	_, targetMessageID, err := ids.ParseMessageID(msg.TargetMessage.ID)
	if err != nil {
		return nil, err
	}

	var newReactions []tg.ReactionClass
	for _, existing := range msg.ExistingReactionsToKeep {
		newReactions, err = tc.appendEmojiID(newReactions, existing.EmojiID)
		if err != nil {
			return nil, err
		}
	}
	newReactions, err = tc.appendEmojiID(newReactions, msg.PreHandleResp.EmojiID)
	if err != nil {
		return nil, err
	}

	_, err = tc.client.API().MessagesSendReaction(ctx, &tg.MessagesSendReactionRequest{
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

func (tc *TelegramClient) HandleMatrixReactionRemove(ctx context.Context, msg *bridgev2.MatrixReactionRemove) error {
	if msg.Portal.RoomType == database.RoomTypeSpace {
		return fmt.Errorf("can't send messages to space portals")
	}
	peer, _, err := tc.inputPeerForPortalID(ctx, msg.Portal.ID)
	if err != nil {
		return err
	}

	var newReactions []tg.ReactionClass

	if maxReactions, err := tc.getReactionLimit(ctx, tc.userID); err != nil {
		return err
	} else if maxReactions > 1 {
		existing, err := tc.main.Bridge.DB.Reaction.GetAllToMessageBySender(ctx, msg.Portal.Receiver, msg.TargetReaction.MessageID, msg.TargetReaction.SenderID)
		if err != nil {
			return err
		}
		for _, existing := range existing {
			if msg.TargetReaction.EmojiID != existing.EmojiID {
				newReactions, err = tc.appendEmojiID(newReactions, existing.EmojiID)
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
	_, err = tc.client.API().MessagesSendReaction(ctx, &tg.MessagesSendReactionRequest{
		Peer:        peer,
		AddToRecent: true,
		MsgID:       messageID,
		Reaction:    newReactions,
	})
	return err
}

func (tc *TelegramClient) HandleMatrixReadReceipt(ctx context.Context, msg *bridgev2.MatrixReadReceipt) error {
	if msg.Portal.RoomType == database.RoomTypeSpace {
		return nil
	}
	log := zerolog.Ctx(ctx).With().
		Str("action", "handle_matrix_read_receipt").
		Str("portal_id", string(msg.Portal.ID)).
		Bool("is_supergroup", msg.Portal.Metadata.(*PortalMetadata).IsSuperGroup).
		Logger()
	peerType, portalID, topicID, parseErr := ids.ParsePortalID(msg.Portal.ID)
	if parseErr != nil {
		return parseErr
	}
	if msg.Portal.Metadata.(*PortalMetadata).IsForumGeneral {
		topicID = 1
	}
	inputPeer, _, parseErr := tc.inputPeerForPortalID(ctx, msg.Portal.ID)
	if parseErr != nil {
		return parseErr
	}

	var readMentionsErr, readReactionsErr, readMessagesErr error
	var wg sync.WaitGroup

	isBot := tc.metadata.IsBot

	// Read mentions
	wg.Add(1)
	go func() {
		defer wg.Done()
		if isBot {
			return
		}
		_, readMentionsErr = tc.client.API().MessagesReadMentions(ctx, &tg.MessagesReadMentionsRequest{
			Peer:     inputPeer,
			TopMsgID: topicID,
		})
	}()

	// Read reactions
	wg.Add(1)
	go func() {
		defer wg.Done()
		if isBot {
			return
		}
		_, readMentionsErr = tc.client.API().MessagesReadReactions(ctx, &tg.MessagesReadReactionsRequest{
			Peer:     inputPeer,
			TopMsgID: topicID,
		})
	}()

	// Read messages
	wg.Add(1)
	go func() {
		defer wg.Done()

		if isBot {
			return
		}

		message := msg.ExactMessage
		if message == nil {
			message, readMessagesErr = tc.main.Bridge.DB.Message.GetLastPartAtOrBeforeTime(ctx, msg.Portal.PortalKey, time.Now())
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
			_, readMessagesErr = tc.client.API().MessagesReadHistory(ctx, &tg.MessagesReadHistoryRequest{
				Peer:  inputPeer,
				MaxID: maxID,
			})
		case ids.PeerTypeChannel:
			var accessHash int64
			accessHash, readMessagesErr = tc.ScopedStore.GetAccessHash(ctx, ids.PeerTypeChannel, portalID)
			if readMessagesErr != nil {
				return
			}
			_, readMessagesErr = tc.client.API().ChannelsReadHistory(ctx, &tg.ChannelsReadHistoryRequest{
				Channel: &tg.InputChannel{ChannelID: portalID, AccessHash: accessHash},
				MaxID:   maxID,
			})

			meta := msg.Portal.Metadata.(*PortalMetadata)
			randomID := meta.SponsoredMessageRandomID
			if !tc.metadata.IsBot &&
				randomID != nil &&
				time.Since(meta.SponsoredMessagePollTS.Time) < 15*time.Minute &&
				(meta.SponsoredMessageEventID == msg.EventID || msg.Receipt.Timestamp.After(meta.SponsoredMessagePollTS.Time)) &&
				meta.sponsoredMessageSeen.Add(tc.telegramUserID) {
				_, viewSponsoredErr := tc.client.API().MessagesViewSponsoredMessage(ctx, randomID)
				if viewSponsoredErr != nil {
					log.Err(viewSponsoredErr).Msg("Failed to mark sponsored message as viewed after read receipt")
				} else {
					log.Debug().
						Str("random_id", base64.StdEncoding.EncodeToString(randomID)).
						Msg("Marked sponsored message as viewed after read receipt")
				}
			}
		default:
			readMessagesErr = fmt.Errorf("unknown peer type %s", peerType)
		}
	}()

	// Poll for reactions (non-blocking to avoid deadlock when portal event buffer is disabled)
	go func() {
		err := tc.maybePollForReactions(ctx, msg.Portal)
		if err != nil {
			log.Err(err).Msg("failed to poll for reactions after read receipt")
		}
		err = tc.pollSponsoredMessage(ctx, msg.Portal)
		if err != nil {
			log.Err(err).Msg("failed to poll for sponsored message after read receipt")
		}
	}()

	if peerType == ids.PeerTypeChannel && !msg.Portal.Metadata.(*PortalMetadata).FullSynced {
		log.Debug().Msg("Scheduling chat resync on read receipt because channel has never got a full sync")
		go tc.userLogin.QueueRemoteEvent(&simplevent.ChatResync{
			EventMeta: simplevent.EventMeta{
				Type:      bridgev2.RemoteEventChatResync,
				PortalKey: msg.Portal.PortalKey,
			},
			GetChatInfoFunc: tc.GetChatInfo,
		})
	}

	wg.Wait()
	return errors.Join(readMentionsErr, readReactionsErr, readMessagesErr)
}

func (tc *TelegramClient) HandleMatrixTyping(ctx context.Context, msg *bridgev2.MatrixTyping) error {
	if msg.Portal.RoomType == database.RoomTypeSpace {
		return nil
	}
	inputPeer, topicID, err := tc.inputPeerForPortalID(ctx, msg.Portal.ID)
	if err != nil {
		return err
	}
	if msg.Portal.Metadata.(*PortalMetadata).IsForumGeneral {
		topicID = 1
	}
	var action tg.SendMessageActionClass
	switch msg.Type {
	case bridgev2.TypingTypeText:
		action = &tg.SendMessageTypingAction{}
	case bridgev2.TypingTypeRecordingMedia:
		// TODO media types?
		action = &tg.SendMessageRecordVideoAction{}
	case bridgev2.TypingTypeUploadingMedia:
		action = &tg.SendMessageUploadVideoAction{}
	}
	if !msg.IsTyping {
		action = &tg.SendMessageCancelAction{}
	}
	_, err = tc.client.API().MessagesSetTyping(ctx, &tg.MessagesSetTypingRequest{
		Peer:     inputPeer,
		TopMsgID: topicID,
		Action:   action,
	})
	return err
}

func (tc *TelegramClient) HandleMatrixDisappearingTimer(ctx context.Context, msg *bridgev2.MatrixDisappearingTimer) (bool, error) {
	inputPeer, topicID, err := tc.inputPeerForPortalID(ctx, msg.Portal.ID)
	if err != nil {
		return false, err
	} else if topicID > 0 {
		return false, fmt.Errorf("topics can't have their own disappearing timer")
	}
	_, err = tc.client.API().MessagesSetHistoryTTL(ctx, &tg.MessagesSetHistoryTTLRequest{
		Peer:   inputPeer,
		Period: int(msg.Content.Timer.Seconds()),
	})
	if err == nil {
		msg.Portal.Disappear = database.DisappearingSetting{
			Type:  event.DisappearingTypeAfterSend,
			Timer: msg.Content.Timer.Duration,
		}.Normalize()
	}
	return err == nil, err
}

func (tc *TelegramClient) HandleMute(ctx context.Context, msg *bridgev2.MatrixMute) error {
	inputPeer, topicID, err := tc.inputPeerForPortalID(ctx, msg.Portal.ID)
	if err != nil {
		return err
	}

	settings := tg.InputPeerNotifySettings{
		Silent:    msg.Content.IsMuted(),
		MuteUntil: int(max(0, min(msg.Content.GetMutedUntilTime().Unix(), math.MaxInt32))),
	}
	var peer tg.InputNotifyPeerClass
	if topicID > 0 {
		peer = &tg.InputNotifyForumTopic{Peer: inputPeer, TopMsgID: topicID}
	} else {
		peer = &tg.InputNotifyPeer{Peer: inputPeer}
	}

	_, err = tc.client.API().AccountUpdateNotifySettings(ctx, &tg.AccountUpdateNotifySettingsRequest{
		Peer:     peer,
		Settings: settings,
	})
	return err
}

func (tc *TelegramClient) HandleRoomTag(ctx context.Context, msg *bridgev2.MatrixRoomTag) error {
	inputPeer, topicID, err := tc.inputPeerForPortalID(ctx, msg.Portal.ID)
	if err != nil {
		return err
	} else if topicID > 0 {
		return fmt.Errorf("topics can't be pinned for yourself")
	}

	_, err = tc.client.API().MessagesToggleDialogPin(ctx, &tg.MessagesToggleDialogPinRequest{
		Pinned: slices.Contains(maps.Keys(msg.Content.Tags), event.RoomTagFavourite),
		Peer:   &tg.InputDialogPeer{Peer: inputPeer},
	})
	return err
}

func (tc *TelegramClient) HandleMatrixDeleteChat(ctx context.Context, chat *bridgev2.MatrixDeleteChat) error {
	peerType, id, topicID, err := ids.ParsePortalID(chat.Portal.ID)
	if err != nil {
		return err
	}
	switch peerType {
	case ids.PeerTypeUser:
		accessHash, err := tc.ScopedStore.GetAccessHash(ctx, peerType, id)
		if err != nil {
			return err
		}
		_, err = tc.client.API().MessagesDeleteHistory(ctx, &tg.MessagesDeleteHistoryRequest{
			Peer:      &tg.InputPeerUser{UserID: id, AccessHash: accessHash},
			JustClear: !chat.Content.DeleteForEveryone,
			Revoke:    chat.Content.DeleteForEveryone,
			MaxID:     0,
		})
		if err != nil {
			return err
		}
	case ids.PeerTypeChat:
		if !chat.Content.DeleteForEveryone {
			return fmt.Errorf("chats can only be deleted for everyone or left")
		}
		result, err := tc.client.API().MessagesDeleteChat(ctx, id)
		if err != nil {
			return err
		}
		if !result {
			return fmt.Errorf("failed to delete chat %d", id)
		}
		return nil
	case ids.PeerTypeChannel:
		if !chat.Content.DeleteForEveryone {
			return fmt.Errorf("channels can only be deleted for everyone or left")
		}
		accessHash, err := tc.ScopedStore.GetAccessHash(ctx, peerType, id)
		if err != nil {
			return err
		}
		if topicID > 0 {
			_, err = tc.client.API().MessagesDeleteTopicHistory(ctx, &tg.MessagesDeleteTopicHistoryRequest{
				Peer: &tg.InputPeerChannel{
					ChannelID:  id,
					AccessHash: accessHash,
				},
				TopMsgID: topicID,
			})
		} else {
			_, err = tc.client.API().ChannelsDeleteChannel(ctx, &tg.InputChannel{
				ChannelID:  id,
				AccessHash: accessHash,
			})
		}
		if err != nil {
			return err
		}
		return nil
	default:
		return fmt.Errorf("unknown peer type %s", peerType)
	}
	return nil
}

func (tc *TelegramClient) HandleMatrixRoomName(ctx context.Context, msg *bridgev2.MatrixRoomName) (bool, error) {
	peerType, id, topicID, err := ids.ParsePortalID(msg.Portal.ID)
	if err != nil {
		return false, err
	}

	switch peerType {
	case ids.PeerTypeChat:
		_, err = tc.client.API().MessagesEditChatTitle(ctx, &tg.MessagesEditChatTitleRequest{
			ChatID: id,
			Title:  msg.Content.Name,
		})
		if err != nil {
			return false, err
		}
		return true, nil
	case ids.PeerTypeChannel:
		accessHash, err := tc.ScopedStore.GetAccessHash(ctx, peerType, id)
		if err != nil {
			return false, err
		}
		if topicID > 0 {
			_, err = tc.client.API().MessagesEditForumTopic(ctx, &tg.MessagesEditForumTopicRequest{
				Peer: &tg.InputPeerChannel{
					ChannelID:  id,
					AccessHash: accessHash,
				},
				TopicID: topicID,
				Title:   msg.Content.Name,
			})
		} else {
			_, err = tc.client.API().ChannelsEditTitle(ctx, &tg.ChannelsEditTitleRequest{
				Channel: &tg.InputChannel{
					ChannelID:  id,
					AccessHash: accessHash,
				},
				Title: msg.Content.Name,
			})
		}
		if err != nil {
			return false, err
		}
		return true, nil
	default:
		return false, fmt.Errorf("unsupported peer type %s for changing room name", peerType)
	}
}

func (tc *TelegramClient) HandleMatrixRoomAvatar(ctx context.Context, msg *bridgev2.MatrixRoomAvatar) (bool, error) {
	peerType, id, topicID, err := ids.ParsePortalID(msg.Portal.ID)
	if err != nil {
		return false, err
	}

	if peerType == ids.PeerTypeUser {
		return false, fmt.Errorf("changing user avatar is not supported")
	} else if topicID > 0 {
		return false, fmt.Errorf("changing group topic avatar is not supported")
	}

	var photo tg.InputChatPhotoClass
	if msg.Content.URL == "" {
		photo = &tg.InputChatPhotoEmpty{}
	} else {
		data, err := tc.main.Bridge.Bot.DownloadMedia(ctx, msg.Content.URL, nil)
		if err != nil {
			return false, fmt.Errorf("failed to download avatar: %w", err)
		}
		upload, err := uploader.NewUploader(tc.client.API()).FromBytes(ctx, "avatar.jpg", data)
		if err != nil {
			return false, fmt.Errorf("failed to upload avatar: %w", err)
		}
		photo = &tg.InputChatUploadedPhoto{File: upload}
	}

	switch peerType {
	case ids.PeerTypeChat:
		_, err = tc.client.API().MessagesEditChatPhoto(ctx, &tg.MessagesEditChatPhotoRequest{
			ChatID: id,
			Photo:  photo,
		})
		if err != nil {
			return false, err
		}
		// TODO update portal metadata
		return true, nil
	case ids.PeerTypeChannel:
		accessHash, err := tc.ScopedStore.GetAccessHash(ctx, peerType, id)
		if err != nil {
			return false, err
		}
		_, err = tc.client.API().ChannelsEditPhoto(ctx, &tg.ChannelsEditPhotoRequest{
			Channel: &tg.InputChannel{
				ChannelID:  id,
				AccessHash: accessHash,
			},
			Photo: photo,
		})
		if err != nil {
			return false, err
		}
		// TODO update portal metadata
		return true, nil
	default:
		return false, fmt.Errorf("unsupported peer type %s for changing room avatar", peerType)
	}
}
