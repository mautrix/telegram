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
	"cmp"
	"context"
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"fmt"
	"html"
	"strconv"
	"strings"
	"time"

	"github.com/rs/zerolog"
	"go.mau.fi/util/exmime"
	"go.mau.fi/util/ptr"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/event"
	"maunium.net/go/mautrix/id"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/connector/media"
	"go.mau.fi/mautrix-telegram/pkg/connector/store"
	"go.mau.fi/mautrix-telegram/pkg/connector/telegramfmt"
	"go.mau.fi/mautrix-telegram/pkg/connector/util"
	"go.mau.fi/mautrix-telegram/pkg/connector/waveform"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tgerr"
)

type spoilable interface {
	GetSpoiler() bool
}

type ttlable interface {
	GetTTLSeconds() (value int, ok bool)
}

func mediaHashID(ctx context.Context, m tg.MessageMediaClass) []byte {
	if m == nil {
		return nil
	}
	switch media := m.(type) {
	case *tg.MessageMediaPhoto:
		if media.Video != nil {
			return binary.BigEndian.AppendUint64(nil, uint64(media.Video.GetID()))
		} else if media.Photo != nil {
			return binary.BigEndian.AppendUint64(nil, uint64(media.Photo.GetID()))
		} else {
			zerolog.Ctx(ctx).Debug().Msg("Attempted to get hash for nil photo")
		}
	case *tg.MessageMediaDocument:
		if media.Document != nil {
			return binary.BigEndian.AppendUint64(nil, uint64(media.Document.GetID()))
		} else {
			zerolog.Ctx(ctx).Debug().Msg("Attempted to get hash for nil document")
		}
	default:
		zerolog.Ctx(ctx).Debug().Type("media_type", m).Msg("Attempted to get hash for unsupported media type ID")
	}
	return nil
}

func (c *TelegramClient) mediaToMatrix(
	ctx context.Context,
	portal *bridgev2.Portal,
	intent bridgev2.MatrixAPI,
	msg *tg.Message,
) (*bridgev2.ConvertedMessagePart, *database.DisappearingSetting, []byte) {
	media, ok := msg.GetMedia()
	if !ok {
		return nil, nil, nil
	}

	switch media.TypeID() {
	case tg.MessageMediaWebPageTypeID:
		// Already handled in the message handling
		return nil, nil, nil
	case tg.MessageMediaUnsupportedTypeID:
		return &bridgev2.ConvertedMessagePart{
			Type: event.EventMessage,
			Content: &event.MessageEventContent{
				MsgType: event.MsgNotice,
				Body:    "This message is not supported on your version of Mautrix-Telegram. Please check https://github.com/mautrix/telegram or ask your bridge administrator about possible updates.",
			},
			Extra: map[string]any{
				"fi.mau.telegram.unsupported": true,
			},
		}, nil, nil
	case tg.MessageMediaPhotoTypeID, tg.MessageMediaDocumentTypeID:
		converted, disappearingSetting := c.convertMediaRequiringUpload(ctx, portal, intent, msg.ID, media, true)
		return converted, disappearingSetting, mediaHashID(ctx, media)
	case tg.MessageMediaContactTypeID:
		return c.convertContact(media), nil, nil
	case tg.MessageMediaGeoTypeID, tg.MessageMediaGeoLiveTypeID, tg.MessageMediaVenueTypeID:
		return convertLocation(media), nil, nil
	case tg.MessageMediaPollTypeID:
		return convertPoll(media), nil, nil
	case tg.MessageMediaDiceTypeID:
		return convertDice(media), nil, nil
	case tg.MessageMediaGameTypeID:
		return convertGame(media), nil, nil
	default:
		// TODO: support these properly
		return &bridgev2.ConvertedMessagePart{
			Type: event.EventMessage,
			Content: &event.MessageEventContent{
				MsgType: event.MsgNotice,
				Body:    fmt.Sprintf("%s are not yet supported. Open Telegram to view.", media.TypeName()),
			},
			Extra: map[string]any{
				"fi.mau.telegram.unsupported": true,
				"fi.mau.telegram.type_id":     media.TypeID(),
			},
		}, nil, nil
	}
}

func (c *TelegramClient) convertToMatrix(
	ctx context.Context,
	portal *bridgev2.Portal,
	intent bridgev2.MatrixAPI,
	msg *tg.Message,
) (cm *bridgev2.ConvertedMessage, err error) {
	log := zerolog.Ctx(ctx).With().Str("conversion_direction", "to_matrix").Logger()
	ctx = log.WithContext(ctx)

	if c.client == nil {
		return nil, fmt.Errorf("telegram client is nil, we are likely logged out")
	}

	var perMessageProfile *event.BeeperPerMessageProfile
	if peerType, _, _, err := ids.ParsePortalID(portal.ID); err != nil {
		return nil, fmt.Errorf("failed to parse portal ID: %w", err)
	} else if peerType == ids.PeerTypeChannel && !portal.Metadata.(*PortalMetadata).IsSuperGroup {
		var sender *networkid.UserID
		if msg.Out {
			sender = &c.userID
		} else if fromID, ok := msg.GetFromID(); ok {
			sender = ptr.Ptr(c.getPeerSender(fromID).Sender)
		}
		if sender != nil {
			profile, err := portal.PerMessageProfileForSender(ctx, *sender)
			if err != nil {
				return nil, fmt.Errorf("failed to get per-message profile for sender %s: %w", *sender, err)
			}
			perMessageProfile = &profile
		}
	}

	cm = &bridgev2.ConvertedMessage{}
	hasher := sha256.New()
	if len(msg.Message) > 0 {
		hasher.Write([]byte(msg.Message))

		content := c.parseBodyAndHTML(ctx, msg.Message, msg.Entities)
		if media, ok := msg.GetMedia(); ok && media.TypeID() == tg.MessageMediaWebPageTypeID {
			webpageCtx, webpageCtxCancel := context.WithTimeout(ctx, time.Second*5)
			defer webpageCtxCancel()
			preview, err := c.webpageToBeeperLinkPreview(webpageCtx, portal, intent, msg, media)
			if err != nil {
				log.Err(err).Msg("Failed to convert webpage to link preview")
			} else if preview != nil {
				hasher.Write([]byte(preview.MatchedURL))
				content.BeeperLinkPreviews = append(content.BeeperLinkPreviews, preview)
			}
		}

		cm.Parts = []*bridgev2.ConvertedMessagePart{{
			Type:    event.EventMessage,
			Content: content,
		}}
	}

	var contentURI id.ContentURIString
	mediaPart, disappearingSetting, mediaHashID := c.mediaToMatrix(ctx, portal, intent, msg)
	if mediaPart != nil {
		hasher.Write(mediaHashID)
		cm.Parts = append(cm.Parts, mediaPart)
		cm.MergeCaption()

		contentURI = mediaPart.Content.URL
		if contentURI == "" && mediaPart.Content.File != nil {
			contentURI = mediaPart.Content.File.URL
		}

		if disappearingSetting != nil {
			cm.Disappear = *disappearingSetting
		}
	}
	cm.Parts[0].Content.BeeperPerMessageProfile = perMessageProfile
	cm.Parts[0].DBMetadata = &MessageMetadata{
		ContentHash: hasher.Sum(nil),
		ContentURI:  contentURI,
	}
	if fwd, isForwarded := msg.GetFwdFrom(); isForwarded {
		err = c.addForwardHeader(ctx, cm.Parts[0], fwd)
		if err != nil {
			return nil, fmt.Errorf("failed to add forward header: %w", err)
		}
	}

	if replyTo, ok := msg.GetReplyTo(); ok {
		switch replyTo := replyTo.(type) {
		case *tg.MessageReplyHeader:
			if (replyTo.ReplyToTopID != 0 || !replyTo.ForumTopic) && replyTo.ReplyToTopID != replyTo.ReplyToMsgID {
				cm.ReplyTo = &networkid.MessageOptionalPartID{}
				if peerID, present := replyTo.GetReplyToPeerID(); present {
					cm.ReplyTo.MessageID = ids.MakeMessageID(peerID, replyTo.ReplyToMsgID)
				} else {
					cm.ReplyTo.MessageID = ids.MakeMessageID(portal.PortalKey, replyTo.ReplyToMsgID)
				}
			}
		default:
			log.Warn().Type("reply_to", replyTo).Msg("unhandled reply to type")
		}
	}

	if ttl, ok := msg.GetTTLPeriod(); ok && disappearingSetting == nil {
		cm.Disappear = database.DisappearingSetting{
			Type:  event.DisappearingTypeAfterSend,
			Timer: time.Duration(ttl) * time.Second,
		}
	}

	return
}

func (t *TelegramClient) addForwardHeader(ctx context.Context, part *bridgev2.ConvertedMessagePart, fwd tg.MessageFwdHeader) error {
	var fwdFromText, fwdFromHTML string
	switch from := fwd.FromID.(type) {
	case *tg.PeerUser:
		user := t.main.Bridge.GetCachedUserLoginByID(ids.MakeUserLoginID(from.UserID))
		var mxid id.UserID
		if user != nil {
			mxid = user.UserMXID
			fwdFromText = cmp.Or(user.RemoteName, user.UserMXID.String())
		} else if ghost, err := t.main.Bridge.GetGhostByID(ctx, ids.MakeUserID(from.UserID)); err != nil {
			return err
		} else {
			if ghost.Name == "" {
				info, err := t.GetUserInfo(ctx, ghost)
				if err != nil {
					zerolog.Ctx(ctx).Warn().Err(err).Msg("Failed to get user info to add forward header")
				} else if info != nil {
					ghost.UpdateInfo(ctx, info)
				}
			}
			mxid = ghost.Intent.GetMXID()
			fwdFromText = cmp.Or(ghost.Name, fwd.FromName, "unknown user")
		}
		fwdFromHTML = fmt.Sprintf(
			`<a href="%s">%s</a>`,
			mxid.URI().MatrixToURL(),
			html.EscapeString(fwdFromText),
		)
	case *tg.PeerChannel, *tg.PeerChat:
		unknownType := "unknown chat"
		var channelID int64
		if ch, ok := from.(*tg.PeerChannel); ok {
			unknownType = "unknown channel"
			channelID = ch.ChannelID
		}
		portal, err := t.main.Bridge.GetExistingPortalByKey(ctx, t.makePortalKeyFromPeer(from, 0))
		if err != nil {
			return err
		} else if portal != nil && portal.MXID != "" {
			fwdFromText = cmp.Or(portal.Name, fwd.FromName, unknownType)
			fwdFromHTML = fmt.Sprintf(
				`<a href="%s">%s</a>`,
				portal.MXID.URI().MatrixToURL(),
				html.EscapeString(fwdFromText),
			)
		} else if fwd.FromName != "" {
			fwdFromText = fwd.FromName
			fwdFromHTML = fmt.Sprintf("<strong>%s</strong>", html.EscapeString(fwd.FromName))
		} else {
			fwdFromText = unknownType
			fwdFromHTML = unknownType
		}
		if channelID != 0 && fwdFromText == unknownType {
			ghost, err := t.main.Bridge.GetExistingGhostByID(ctx, ids.MakeChannelUserID(channelID))
			if err != nil {
				return err
			} else if ghost != nil && ghost.Name != "" {
				fwdFromText = ghost.Name
				fwdFromHTML = fmt.Sprintf(
					`<a href="%s">%s</a>`,
					ghost.Intent.GetMXID().URI().MatrixToURL(),
					html.EscapeString(fwdFromText),
				)
			}
		}
		// TODO fetch channel if not found
	}
	if fwdFromText == "" && fwd.FromName != "" {
		fwdFromText = fwd.FromName
		fwdFromHTML = fmt.Sprintf("<strong>%s</strong>", html.EscapeString(fwd.FromName))
	}
	if fwdFromText == "" {
		fwdFromText = "unknown source"
		fwdFromHTML = fwdFromText
	}

	if part.Content.MsgType.IsMedia() {
		if part.Content.FileName == "" {
			part.Content.FileName = part.Content.Body
		}
		if part.Content.Body == part.Content.FileName {
			part.Content.Body = ""
		}
	}

	part.Content.EnsureHasHTML()
	existingBodyLines := strings.Split(part.Content.Body, "\n")
	for i, line := range existingBodyLines {
		existingBodyLines[i] = fmt.Sprintf("> %s", line)
	}
	if len(existingBodyLines) > 0 {
		existingBodyLines = append([]string{"\n"}, existingBodyLines...)
	}
	part.Content.Body = fmt.Sprintf(
		"Forwarded message from %s%s",
		fwdFromText, strings.Join(existingBodyLines, "\n"),
	)
	existingFormattedBody := part.Content.FormattedBody
	if existingFormattedBody != "" {
		existingFormattedBody = fmt.Sprintf("<br><tg-forward><blockquote>%s</blockquote></tg-forward>", existingFormattedBody)
	}
	part.Content.FormattedBody = fmt.Sprintf(
		"Forwarded message from %s%s",
		fwdFromHTML, existingFormattedBody,
	)
	return nil
}

func (t *TelegramClient) parseBodyAndHTML(ctx context.Context, message string, entities []tg.MessageEntityClass) *event.MessageEventContent {
	if len(entities) == 0 {
		return &event.MessageEventContent{MsgType: event.MsgText, Body: message}
	}

	var customEmojiIDs []int64
	for _, entity := range entities {
		switch entity := entity.(type) {
		case *tg.MessageEntityCustomEmoji:
			customEmojiIDs = append(customEmojiIDs, entity.DocumentID)
		}
	}
	customEmojis, err := t.transferEmojisToMatrix(ctx, customEmojiIDs)
	if err != nil {
		zerolog.Ctx(ctx).Err(err).
			Ints64("emoji_ids", customEmojiIDs).
			Msg("Failed to transfer custom emojis to Matrix")
	}
	return telegramfmt.Parse(ctx, message, entities, t.telegramFmtParams.WithCustomEmojis(customEmojis))
}

func (c *TelegramClient) webpageToBeeperLinkPreview(ctx context.Context, portal *bridgev2.Portal, intent bridgev2.MatrixAPI, msg *tg.Message, msgMedia tg.MessageMediaClass) (preview *event.BeeperLinkPreview, err error) {
	webpage, ok := msgMedia.(*tg.MessageMediaWebPage).Webpage.(*tg.WebPage)
	if !ok {
		return nil, nil
	}
	preview = &event.BeeperLinkPreview{
		MatchedURL: webpage.URL,
		LinkPreview: event.LinkPreview{
			Title:        webpage.Title,
			CanonicalURL: webpage.URL,
			Description:  webpage.Description,
		},
	}

	if photo, ok := webpage.Photo.(*tg.Photo); ok {
		var fileInfo *event.FileInfo
		transferer := media.NewTransferer(c.client.API()).WithPhoto(photo)
		if c.main.useDirectMedia {
			preview.ImageURL, fileInfo, err = transferer.DirectDownloadURL(ctx, c.telegramUserID, portal, msg.ID, true, 0)
		} else {
			preview.ImageURL, preview.ImageEncryption, fileInfo, err = transferer.Transfer(ctx, c.main.Store, intent)
		}
		if err != nil {
			return nil, err
		}
		preview.ImageSize = event.IntOrString(fileInfo.Size)
		preview.ImageWidth = event.IntOrString(fileInfo.Width)
		preview.ImageHeight = event.IntOrString(fileInfo.Height)
		preview.ImageType = fileInfo.MimeType
		if fileInfo.MimeType == "application/octet-stream" {
			preview.ImageType = "image/jpeg"
		}
	}

	return preview, nil
}

func (c *TelegramClient) convertMediaRequiringUpload(
	ctx context.Context,
	portal *bridgev2.Portal,
	intent bridgev2.MatrixAPI,
	msgID int,
	msgMedia tg.MessageMediaClass,
	allowRefetch bool,
) (converted *bridgev2.ConvertedMessagePart, disappearingSetting *database.DisappearingSetting) {
	log := zerolog.Ctx(ctx).With().
		Str("conversion_direction", "to_matrix").
		Str("portal_id", string(portal.ID)).
		Int("msg_id", msgID).
		Logger()
	eventType := event.EventMessage
	var content event.MessageEventContent
	var telegramMediaID int64
	var isSticker, isVideo, isVideoGif bool
	extra := map[string]any{}
	// FIXME don't use raw map for fields in the FileInfo struct
	extraInfo := map[string]any{}

	transferer := media.NewTransferer(c.client.API()).WithRoomID(portal.MXID)
	var mediaTransferer *media.ReadyTransferer

	if t, ok := msgMedia.(ttlable); ok {
		if ttl, ok := t.GetTTLSeconds(); ok {
			typeName := "photo"
			if msgMedia.TypeID() == tg.MessageMediaDocumentTypeID {
				typeName = "file"
			}

			if ttl == 2147483647 {
				// This is a view-once message, set a low TTL.
				ttl = 15

				if c.main.Config.DisableViewOnce {
					converted = &bridgev2.ConvertedMessagePart{
						Type: event.EventMessage,
						Content: &event.MessageEventContent{
							MsgType: event.MsgNotice,
							Body:    fmt.Sprintf("You received a view once %s. For added privacy, you can only open it on the Telegram app.", typeName),
						},
					}
					return
				}
			}

			disappearingSetting = &database.DisappearingSetting{
				// Even though normal message TTLs are after send, media is after read
				Type:  event.DisappearingTypeAfterRead,
				Timer: time.Duration(ttl) * time.Second,
			}
		}
	}

	// Determine the filename and some other information
	switch msgMedia := msgMedia.(type) {
	case *tg.MessageMediaPhoto:
		content.MsgType = event.MsgImage
		if disappearingSetting != nil {
			content.Body = "disappearing_image"
		} else {
			content.Body = "image"
		}
		photo, ok := msgMedia.Photo.(*tg.Photo)
		if !ok {
			converted = &bridgev2.ConvertedMessagePart{
				Type: event.EventMessage,
				Content: &event.MessageEventContent{
					MsgType: event.MsgNotice,
					Body:    "Unsupported photo message. Check Telegram app.",
				},
			}
			return
		}
		if video, ok := msgMedia.Video.(*tg.Document); ok {
			content.MsgType = event.MsgVideo
			telegramMediaID = video.GetID()
			mediaTransferer = transferer.WithLivePhoto(photo, video)
			extraInfo["fi.mau.telegram.live_photo"] = true

			// TODO deduplicate with document thumbnail code
			var thumbnailURL id.ContentURIString
			var thumbnailFile *event.EncryptedFileInfo
			var thumbnailInfo *event.FileInfo
			var err error

			thumbnailTransferer := media.NewTransferer(c.client.API()).
				WithRoomID(portal.MXID).
				WithPhoto(photo)
			if c.main.useDirectMedia {
				thumbnailURL, thumbnailInfo, err = thumbnailTransferer.DirectDownloadURL(ctx, c.telegramUserID, portal, msgID, false, photo.ID)
				if err != nil {
					log.Err(err).Msg("Failed to create direct download URL for thumbnail")
				}
			}
			if thumbnailURL == "" {
				thumbnailURL, thumbnailFile, thumbnailInfo, err = thumbnailTransferer.Transfer(ctx, c.main.Store, intent)
				if err != nil {
					log.Err(err).Msg("Failed to transfer thumbnail")
				}
			}
			if thumbnailURL != "" || thumbnailFile != nil {
				transferer = transferer.WithThumbnail(thumbnailURL, thumbnailFile, thumbnailInfo)
			}
		} else {
			telegramMediaID = photo.GetID()
			mediaTransferer = transferer.WithPhoto(photo)
		}
	case *tg.MessageMediaDocument:
		document, ok := msgMedia.Document.(*tg.Document)
		if !ok {
			converted = &bridgev2.ConvertedMessagePart{
				Type: event.EventMessage,
				Content: &event.MessageEventContent{
					MsgType: event.MsgNotice,
					Body:    "Unsupported document message. Check Telegram app.",
				},
			}
			return
		}
		telegramMediaID = document.GetID()

		content.MsgType = event.MsgFile

		for _, attr := range document.GetAttributes() {
			switch a := attr.(type) {
			case *tg.DocumentAttributeFilename:
				if content.Body == "" {
					content.Body = a.GetFileName()
				} else {
					content.FileName = a.GetFileName()
				}
			case *tg.DocumentAttributeVideo:
				isVideo = true
				content.MsgType = event.MsgVideo
				transferer = transferer.WithVideo(a)

				if a.RoundMessage {
					extraInfo["fi.mau.telegram.round_message"] = a.RoundMessage
				}
				extraInfo["duration"] = int(a.Duration * 1000)
			case *tg.DocumentAttributeAudio:
				if content.MsgType != event.MsgVideo {
					content.MsgType = event.MsgAudio
					extraInfo["duration"] = int(a.Duration * 1000) // only set the duration is not already set by the video handling logic
				}
				content.MSC1767Audio = &event.MSC1767Audio{
					Duration: a.Duration * 1000,
				}
				if wf, ok := a.GetWaveform(); ok {
					for _, v := range waveform.Decode(wf) {
						content.MSC1767Audio.Waveform = append(content.MSC1767Audio.Waveform, int(v)<<5)
					}
				}
				if a.Voice {
					content.MSC3245Voice = &event.MSC3245Voice{}
				}
			case *tg.DocumentAttributeImageSize:
				transferer = transferer.WithImageSize(a)
			case *tg.DocumentAttributeSticker:
				isSticker = true
				if content.Body == "" {
					content.Body = a.Alt
				} else {
					content.FileName = content.Body
					content.Body = a.Alt
				}
				stickerInfo := map[string]any{"alt": a.Alt, "id": strconv.FormatInt(document.ID, 10)}

				if setID, ok := a.Stickerset.(*tg.InputStickerSetID); ok {
					stickerInfo["pack"] = map[string]any{
						"id":          strconv.FormatInt(setID.ID, 10),
						"access_hash": strconv.FormatInt(setID.AccessHash, 10),
					}
				} else if shortName, ok := a.Stickerset.(*tg.InputStickerSetShortName); ok {
					stickerInfo["pack"] = map[string]any{
						"short_name": shortName.ShortName,
					}
				}
				extraInfo["fi.mau.telegram.sticker"] = stickerInfo
				transferer = transferer.WithStickerConfig(c.main.Config.AnimatedSticker)
			case *tg.DocumentAttributeAnimated:
				isVideoGif = true
				extraInfo["fi.mau.telegram.gif"] = true
			}
		}

		if content.FileName == "" {
			if content.Body != "" {
				content.FileName = content.Body
			} else {
				content.Body = "file"
			}
		}

		if isSticker {
			// Strip filename so that we never render the caption
			content.FileName = ""

			if c.main.Config.AnimatedSticker.Target == "webm" || (isVideo && !c.main.Config.AnimatedSticker.ConvertFromWebm) {
				isVideoGif = true
				extraInfo["fi.mau.telegram.animated_sticker"] = true
				transferer.WithMIMEType("video/webm")
			} else {
				eventType = event.EventSticker
				content.MsgType = "" // Strip the msgtype since that doesn't apply for stickers
			}
		}

		if isVideoGif {
			extraInfo["fi.mau.gif"] = true
			extraInfo["fi.mau.loop"] = true
			extraInfo["fi.mau.autoplay"] = true
			extraInfo["fi.mau.hide_controls"] = true
			extraInfo["fi.mau.no_audio"] = true
		}

		if _, ok := document.GetThumbs(); ok && eventType != event.EventSticker {
			var thumbnailURL id.ContentURIString
			var thumbnailFile *event.EncryptedFileInfo
			var thumbnailInfo *event.FileInfo
			var err error

			thumbnailTransferer := media.NewTransferer(c.client.API()).
				WithRoomID(portal.MXID).
				WithDocument(document, true)
			if c.main.useDirectMedia {
				thumbnailURL, thumbnailInfo, err = thumbnailTransferer.DirectDownloadURL(ctx, c.telegramUserID, portal, msgID, true, document.ID)
				if err != nil {
					log.Err(err).Msg("Failed to create direct download URL for thumbnail")
				}
			}
			if thumbnailURL == "" {
				thumbnailURL, thumbnailFile, thumbnailInfo, err = thumbnailTransferer.Transfer(ctx, c.main.Store, intent)
				if err != nil {
					log.Err(err).Msg("Failed to transfer thumbnail")
				}
			}
			if thumbnailURL != "" || thumbnailFile != nil {
				transferer = transferer.WithThumbnail(thumbnailURL, thumbnailFile, thumbnailInfo)
			}
		}

		mediaTransferer = transferer.
			WithFilename(content.Body).
			WithDocument(msgMedia.Document, false)
	default:
		converted = &bridgev2.ConvertedMessagePart{
			Type: event.EventMessage,
			Content: &event.MessageEventContent{
				MsgType: event.MsgNotice,
				Body:    "Unsupported media message. Check Telegram app.",
			},
		}
		return
	}

	var err error
	if c.main.useDirectMedia && (!isSticker || c.main.Config.AnimatedSticker.Target == "disable") {
		content.URL, content.Info, err = mediaTransferer.DirectDownloadURL(ctx, c.telegramUserID, portal, msgID, false, telegramMediaID)
		if err != nil {
			log.Err(err).Msg("Failed to create direct download URL for media")
		}
	}
	if content.URL == "" {
		content.URL, content.File, content.Info, err = mediaTransferer.Transfer(ctx, c.main.Store, intent)
		if err != nil {
			if tgerr.Is(err, tg.ErrFileReferenceExpired) && allowRefetch {
				log.Warn().Err(err).Msg("Failed to transfer media, trying to refetch from message")
				peerType, peerID, _, err := ids.ParsePortalID(portal.ID)
				if err != nil {
					log.Err(err).Msg("Failed to parse portal ID to refetch media")
				} else if msgMedia, err = c.refetchMedia(ctx, peerType, peerID, msgID); err != nil {
					log.Err(err).Msg("Failed to refetch media after file reference expired error")
				} else {
					return c.convertMediaRequiringUpload(ctx, portal, intent, msgID, msgMedia, false)
				}
			} else {
				log.Err(err).Msg("Failed to transfer media")
			}
			converted = &bridgev2.ConvertedMessagePart{
				Type: event.EventMessage,
				Content: &event.MessageEventContent{
					MsgType: event.MsgNotice,
					Body:    "Failed to transfer media. Check Telegram app.",
				},
			}
			return
		}
		if msgMedia.TypeID() == tg.MessageMediaPhotoTypeID {
			content.Body = content.Body + exmime.ExtensionFromMimetype(content.Info.MimeType)
		}
	}

	// Handle spoilers
	// See: https://github.com/matrix-org/matrix-spec-proposals/pull/3725
	if s, ok := msgMedia.(spoilable); ok && s.GetSpoiler() {
		extra["town.robin.msc3725.content_warning"] = map[string]any{
			"type": "town.robin.msc3725.spoiler",
		}
		extra["page.codeberg.everypizza.msc4193.spoiler"] = true
		extraInfo["fi.mau.telegram.spoiler"] = true
	}
	if len(extraInfo) > 0 {
		extra["info"] = extraInfo
	}

	converted = &bridgev2.ConvertedMessagePart{
		Type:    eventType,
		Content: &content,
		Extra:   extra,
	}
	return
}

func (c *TelegramClient) convertContact(media tg.MessageMediaClass) *bridgev2.ConvertedMessagePart {
	contact := media.(*tg.MessageMediaContact)
	name := util.FormatFullName(contact.FirstName, contact.LastName, false, contact.UserID)
	formattedPhone := fmt.Sprintf("+%s", strings.TrimPrefix(contact.PhoneNumber, "+"))

	content := event.MessageEventContent{
		MsgType: event.MsgText,
		Body:    fmt.Sprintf("Shared contact info for %s: %s", name, formattedPhone),
	}
	if contact.UserID > 0 {
		content.Format = event.FormatHTML
		content.FormattedBody = fmt.Sprintf(
			`Shared contact info for <a href="%s">%s</a>: %s`,
			c.main.Bridge.Matrix.GhostIntent(ids.MakeUserID(contact.UserID)).GetMXID().URI().MatrixToURL(),
			html.EscapeString(name),
			html.EscapeString(formattedPhone),
		)
	}

	return &bridgev2.ConvertedMessagePart{
		Type:    event.EventMessage,
		Content: &content,
		Extra: map[string]any{
			"fi.mau.telegram.contact": map[string]any{
				"user_id":      contact.UserID,
				"first_name":   contact.FirstName,
				"last_name":    contact.LastName,
				"phone_number": contact.PhoneNumber,
				"vcard":        contact.Vcard,
			},
		},
	}
}

type hasGeo interface {
	GetGeo() tg.GeoPointClass
}

func convertLocation(media tg.MessageMediaClass) *bridgev2.ConvertedMessagePart {
	g, ok := media.(hasGeo)
	if !ok || g.GetGeo().TypeID() != tg.GeoPointTypeID {
		return &bridgev2.ConvertedMessagePart{
			Type: event.EventMessage,
			Content: &event.MessageEventContent{
				MsgType: event.MsgNotice,
				Body:    "Unsupported location message. Check Telegram app.",
			},
		}
	}
	point := g.GetGeo().(*tg.GeoPoint)
	var longChar, latChar string
	if point.Long > 0 {
		longChar = "E"
	} else {
		longChar = "W"
	}
	if point.Lat > 0 {
		latChar = "N"
	} else {
		latChar = "S"
	}

	geo := fmt.Sprintf("%f,%f", point.Lat, point.Long)
	geoURI := GeoURIFromLatLong(point.Lat, point.Long).URI()
	body := fmt.Sprintf("%.4f° %s, %.4f° %s", point.Lat, latChar, point.Long, longChar)
	url := fmt.Sprintf("https://maps.google.com/?q=%s", geo)

	extra := map[string]any{}
	var note string
	if media.TypeID() == tg.MessageMediaGeoLiveTypeID {
		note = "Live Location (see your Telegram client for live updates)"
	} else if venue, ok := media.(*tg.MessageMediaVenue); ok {
		note = venue.Title
		body = fmt.Sprintf("%s (%s)", venue.Address, body)
		extra["fi.mau.telegram.venue_id"] = venue.VenueID
	} else {
		note = "Location"
	}

	extra["org.matrix.msc3488.location"] = map[string]any{
		"uri":         geoURI,
		"description": note,
	}

	return &bridgev2.ConvertedMessagePart{
		Type: event.EventMessage,
		Content: &event.MessageEventContent{
			MsgType:       event.MsgLocation,
			GeoURI:        geoURI,
			Body:          fmt.Sprintf("%s: %s\n%s", note, body, url),
			Format:        event.FormatHTML,
			FormattedBody: fmt.Sprintf(`%s: <a href="%s">%s</a>`, note, url, body),
		},
		Extra: extra,
	}
}

func convertPoll(media tg.MessageMediaClass) *bridgev2.ConvertedMessagePart {
	// TODO (PLAT-25224) make this richer in the future once megabridge has support for polls

	poll := media.(*tg.MessageMediaPoll)
	var textAnswers []string
	var htmlAnswers strings.Builder
	for i, opt := range poll.Poll.Answers {
		text := opt.GetText()
		textAnswers = append(textAnswers, fmt.Sprintf("%d. %s", i+1, text.Text))
		htmlAnswers.WriteString(fmt.Sprintf("<li>%s</li>", text.Text))
	}

	return &bridgev2.ConvertedMessagePart{
		Type: event.EventMessage,
		Content: &event.MessageEventContent{
			MsgType:       event.MsgText,
			Body:          fmt.Sprintf("Poll: %s\n%s\nOpen the Telegram app to vote.", poll.Poll.Question.Text, strings.Join(textAnswers, "\n")),
			Format:        event.FormatHTML,
			FormattedBody: fmt.Sprintf(`<strong>Poll</strong>: %s<ol>%s</ol>Open the Telegram app to vote.`, poll.Poll.Question.Text, htmlAnswers.String()),
		},
	}
}

func convertDice(media tg.MessageMediaClass) *bridgev2.ConvertedMessagePart {
	roll := media.(*tg.MessageMediaDice)

	var result string
	var text strings.Builder
	text.WriteString(roll.Emoticon)

	switch roll.Emoticon {
	case "🎯":
		text.WriteString(" Dart throw")
	case "🎲":
		text.WriteString(" Dice roll")
	case "🏀":
		text.WriteString(" Basketball throw")
	case "🎰":
		text.WriteString(" Slot machine")
		emojis := map[int]string{
			0: "🍫",
			1: "🍒",
			2: "🍋",
			3: "7️⃣",
		}
		res := roll.Value - 1
		result = fmt.Sprintf("%s %s %s", emojis[res%4], emojis[res/4%4], emojis[res/16])
	case "🎳":
		text.WriteString(" Bowling")
		result = map[int]string{
			1: "miss",
			2: "1 pin down",
			3: "3 pins down, split",
			4: "4 pins down, split",
			5: "5 pins down",
			6: "strike 🎉",
		}[roll.Value]
	case "⚽":
		text.WriteString(" Football kick")
		result = map[int]string{
			1: "miss",
			2: "hit the woodwork",
			3: "goal", // seems to go in through the center
			4: "goal",
			5: "goal 🎉", // seems to go in through the top right corner, includes confetti
		}[roll.Value]
	}

	text.WriteString(" result: ")
	if len(result) > 0 {
		text.WriteString(result)
		text.WriteString(fmt.Sprintf(" (%d)", roll.Value))
	} else {
		text.WriteString(fmt.Sprintf("%d", roll.Value))
	}

	return &bridgev2.ConvertedMessagePart{
		Type: event.EventMessage,
		Content: &event.MessageEventContent{
			MsgType:       event.MsgText,
			Body:          text.String(),
			Format:        event.FormatHTML,
			FormattedBody: fmt.Sprintf("<h4>%s</h4>", text.String()),
		},
		Extra: map[string]any{
			"fi.mau.telegram.dice": map[string]any{
				"emoticon": roll.Emoticon,
				"value":    roll.Value,
			},
		},
	}
}

func convertGame(media tg.MessageMediaClass) *bridgev2.ConvertedMessagePart {
	// TODO (PLAT-25562) provide a richer experience for the game
	game := media.(*tg.MessageMediaGame)
	return &bridgev2.ConvertedMessagePart{
		Type: event.EventMessage,
		Content: &event.MessageEventContent{
			MsgType: event.MsgText,
			Body:    fmt.Sprintf("Game: %s. Open the Telegram app to play.", game.Game.Title),
		},
	}
}

func (c *TelegramClient) convertUserProfilePhoto(ctx context.Context, user *tg.User, photo *tg.UserProfilePhoto) (*bridgev2.Avatar, error) {
	avatar := &bridgev2.Avatar{
		ID: ids.MakeAvatarID(photo.PhotoID),
	}

	if c.main.useDirectMedia {
		mediaID, err := ids.DirectMediaInfo{
			PeerType: ids.PeerTypeUser,
			PeerID:   user.ID,
			UserID:   c.telegramUserID,
			ID:       photo.PhotoID,
		}.AsMediaID()
		if err != nil {
			return nil, err
		}

		if avatar.MXC, err = c.main.Bridge.Matrix.GenerateContentURI(ctx, mediaID); err != nil {
			return nil, err
		}
		avatar.Hash = ids.HashMediaID(mediaID)
	} else {
		avatar.Get = func(ctx context.Context) (data []byte, err error) {
			// TODO determine if it's safe to unconditionally use the access hash from the user object here
			peer, err := c.getInputPeerUser(ctx, user.ID)
			if errors.Is(err, store.ErrNoAccessHash) {
				peer = &tg.InputPeerUser{
					UserID:     user.ID,
					AccessHash: user.AccessHash,
				}
				if user.Min && c.metadata.IsBot {
					// Bots should use a zero access hash when only a min hash is available
					peer.AccessHash = 0
				}
			} else if err != nil {
				return nil, fmt.Errorf("failed to get peer: %w", err)
			}
			return media.NewTransferer(c.client.API()).WithPeerPhoto(peer, photo.PhotoID).DownloadBytes(ctx)
		}
	}

	return avatar, nil
}

func (c *TelegramClient) convertChatPhoto(chat tg.InputPeerClass, rawChatPhoto tg.ChatPhotoClass) (*bridgev2.Avatar, error) {
	var chatPhoto *tg.ChatPhoto
	switch typedChatPhoto := rawChatPhoto.(type) {
	case *tg.ChatPhotoEmpty:
		return &bridgev2.Avatar{Remove: true}, nil
	case *tg.ChatPhoto:
		chatPhoto = typedChatPhoto
	default:
		return nil, fmt.Errorf("not a chat photo: %T", rawChatPhoto)
	}
	avatar := &bridgev2.Avatar{
		ID: ids.MakeAvatarID(chatPhoto.PhotoID),
	}

	if c.main.useDirectMedia {
		var peerID int64
		var peerType ids.PeerType
		switch typedChat := chat.(type) {
		case *tg.InputPeerChannel:
			peerID = typedChat.ChannelID
			peerType = ids.PeerTypeChannel
		case *tg.InputPeerChat:
			peerID = typedChat.ChatID
			peerType = ids.PeerTypeChat
		case *tg.InputPeerUser:
			peerID = typedChat.UserID
			peerType = ids.PeerTypeUser
		default:
			return nil, fmt.Errorf("unsupported chat type for chat photo: %T", chat)
		}
		mediaID, err := ids.DirectMediaInfo{
			PeerType: peerType,
			PeerID:   peerID,
			UserID:   c.telegramUserID,
			ID:       chatPhoto.PhotoID,
		}.AsMediaID()
		if err != nil {
			return nil, err
		}

		todoRemove := c.main.Bridge.BackgroundCtx // TODO remove context parameter from GenerateContentURI
		if avatar.MXC, err = c.main.Bridge.Matrix.GenerateContentURI(todoRemove, mediaID); err != nil {
			return nil, err
		}
		avatar.Hash = ids.HashMediaID(mediaID)
	} else {
		avatar.Get = func(ctx context.Context) (data []byte, err error) {
			return media.NewTransferer(c.client.API()).WithPeerPhoto(chat, chatPhoto.PhotoID).DownloadBytes(ctx)
		}
	}

	return avatar, nil
}

func (c *TelegramClient) convertPhoto(ctx context.Context, peerType ids.PeerType, peerID int64, photoClass tg.PhotoClass) (*bridgev2.Avatar, error) {
	photo, ok := photoClass.(*tg.Photo)
	if !ok {
		return nil, fmt.Errorf("not a photo: %T", photoClass)
	}

	avatar := &bridgev2.Avatar{
		ID: ids.MakeAvatarID(photo.GetID()),
	}

	if c.main.useDirectMedia {
		mediaID, err := ids.DirectMediaInfo{
			PeerType: peerType,
			PeerID:   peerID,
			UserID:   c.telegramUserID,
			ID:       photo.GetID(),
		}.AsMediaID()
		if err != nil {
			return nil, err
		}

		if avatar.MXC, err = c.main.Bridge.Matrix.GenerateContentURI(ctx, mediaID); err != nil {
			return nil, err
		}

		avatar.Hash = ids.HashMediaID(mediaID)
	} else {
		avatar.Get = func(ctx context.Context) (data []byte, err error) {
			return media.NewTransferer(c.client.API()).WithPhoto(photo).DownloadBytes(ctx)
		}
	}

	return avatar, nil
}
