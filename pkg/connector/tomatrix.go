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
	"encoding/binary"
	"fmt"
	"html"
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

	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tgerr"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/connector/media"
	"go.mau.fi/mautrix-telegram/pkg/connector/telegramfmt"
	"go.mau.fi/mautrix-telegram/pkg/connector/util"
	"go.mau.fi/mautrix-telegram/pkg/connector/waveform"
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
		return binary.BigEndian.AppendUint64(nil, uint64(media.Photo.GetID()))
	case *tg.MessageMediaDocument:
		return binary.BigEndian.AppendUint64(nil, uint64(media.Document.GetID()))
	default:
		zerolog.Ctx(ctx).Error().Type("media_type", m).Msg("Attempted to get hash for unsupported media type ID")
	}
	return nil
}

func (c *TelegramClient) mediaToMatrix(ctx context.Context, portal *bridgev2.Portal, intent bridgev2.MatrixAPI, msg *tg.Message) (*bridgev2.ConvertedMessagePart, *database.DisappearingSetting, []byte, error) {
	media, ok := msg.GetMedia()
	if !ok {
		return nil, nil, nil, nil
	}

	switch media.TypeID() {
	case tg.MessageMediaWebPageTypeID:
		// Already handled in the message handling
		return nil, nil, nil, nil
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
		}, nil, nil, nil
	case tg.MessageMediaPhotoTypeID, tg.MessageMediaDocumentTypeID:
		converted, disappearingSetting, err := c.convertMediaRequiringUpload(ctx, portal, intent, msg.ID, media)
		return converted, disappearingSetting, mediaHashID(ctx, media), err
	case tg.MessageMediaContactTypeID:
		return c.convertContact(media), nil, nil, nil
	case tg.MessageMediaGeoTypeID, tg.MessageMediaGeoLiveTypeID, tg.MessageMediaVenueTypeID:
		location, err := convertLocation(media)
		return location, nil, nil, err
	case tg.MessageMediaPollTypeID:
		return convertPoll(media), nil, nil, nil
	case tg.MessageMediaDiceTypeID:
		return convertDice(media), nil, nil, nil
	case tg.MessageMediaGameTypeID:
		return convertGame(media), nil, nil, nil
	case tg.MessageMediaStoryTypeID, tg.MessageMediaInvoiceTypeID, tg.MessageMediaGiveawayTypeID, tg.MessageMediaGiveawayResultsTypeID:
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
		}, nil, nil, nil
	default:
		return nil, nil, nil, fmt.Errorf("unsupported media type %T", media)
	}
}

func (c *TelegramClient) convertToMatrixWithRefetch(ctx context.Context, portal *bridgev2.Portal, intent bridgev2.MatrixAPI, msg *tg.Message) (cm *bridgev2.ConvertedMessage, err error) {
	cm, err = c.convertToMatrix(ctx, portal, intent, msg)
	if !tgerr.Is(err, tg.ErrFileReferenceExpired) {
		return cm, err
	}

	// If the error is that the file reference expired, refetch the message and
	// try to convert it again.
	log := zerolog.Ctx(ctx).With().Bool("message_refetch", true).Logger()
	ctx = log.WithContext(ctx)
	log.Warn().Err(err).Msg("Refetching message to convert media")

	// TODO deduplicate this with the direct download code
	var m tg.MessagesMessagesClass
	peerType, id, err := ids.ParsePortalID(portal.ID)
	if err != nil {
		return nil, fmt.Errorf("failed to parse portal ID: %w", err)
	} else if peerType == ids.PeerTypeChannel {
		var accessHash int64
		accessHash, err = c.ScopedStore.GetAccessHash(ctx, ids.PeerTypeChannel, id)
		if err != nil {
			return nil, fmt.Errorf("failed to get channel access hash: %w", err)
		}
		m, err = c.client.API().ChannelsGetMessages(ctx, &tg.ChannelsGetMessagesRequest{
			Channel: &tg.InputChannel{
				ChannelID:  id,
				AccessHash: accessHash,
			},
			ID: []tg.InputMessageClass{
				&tg.InputMessageID{ID: msg.ID},
			},
		})
	} else {
		m, err = c.client.API().MessagesGetMessages(ctx, []tg.InputMessageClass{
			&tg.InputMessageID{ID: msg.ID},
		})
	}

	if err != nil {
		return nil, err
	} else if messages, ok := m.(tg.ModifiedMessagesMessages); !ok {
		return nil, fmt.Errorf("unsupported messages type %T", messages)
	} else if len(messages.GetMessages()) != 1 {
		return nil, fmt.Errorf("wrong number of messages retrieved %d", len(messages.GetMessages()))
	} else if refetchedMsg, ok := messages.GetMessages()[0].(*tg.Message); !ok {
		return nil, fmt.Errorf("message was of the wrong type %s", messages.GetMessages()[0].TypeName())
	} else if refetchedMsg.ID != msg.ID {
		return nil, fmt.Errorf("no media found with ID %d", msg.ID)
	} else {
		return c.convertToMatrix(ctx, portal, intent, refetchedMsg)
	}
}

func (c *TelegramClient) convertToMatrix(ctx context.Context, portal *bridgev2.Portal, intent bridgev2.MatrixAPI, msg *tg.Message) (cm *bridgev2.ConvertedMessage, err error) {
	log := zerolog.Ctx(ctx).With().Str("conversion_direction", "to_matrix").Logger()
	ctx = log.WithContext(ctx)

	if c.client == nil {
		return nil, fmt.Errorf("telegram client is nil, we are likely logged out")
	}

	var perMessageProfile *event.BeeperPerMessageProfile
	if peerType, _, err := ids.ParsePortalID(portal.ID); err != nil {
		return nil, err
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
				return nil, err
			}
			perMessageProfile = &profile
		}
	}

	cm = &bridgev2.ConvertedMessage{}
	hasher := sha256.New()
	if len(msg.Message) > 0 {
		hasher.Write([]byte(msg.Message))

		content, err := c.parseBodyAndHTML(ctx, msg.Message, msg.Entities)
		if err != nil {
			return nil, err
		}
		if media, ok := msg.GetMedia(); ok && media.TypeID() == tg.MessageMediaWebPageTypeID {
			webpageCtx, webpageCtxCancel := context.WithTimeout(ctx, time.Second*5)
			defer webpageCtxCancel()
			preview, err := c.webpageToBeeperLinkPreview(webpageCtx, portal, intent, msg, media)
			if err != nil {
				log.Err(err).Msg("error converting webpage to link preview")
			} else if preview != nil {
				hasher.Write([]byte(preview.MatchedURL))
				content.BeeperLinkPreviews = append(content.BeeperLinkPreviews, preview)
			}
		}

		cm.Parts = []*bridgev2.ConvertedMessagePart{
			{
				Type:    event.EventMessage,
				Content: content,
			},
		}
	}

	var contentURI id.ContentURIString
	mediaPart, disappearingSetting, mediaHashID, err := c.mediaToMatrix(ctx, portal, intent, msg)
	if err != nil {
		return nil, err
	} else if mediaPart != nil {
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

	if replyTo, ok := msg.GetReplyTo(); ok {
		switch replyTo := replyTo.(type) {
		case *tg.MessageReplyHeader:
			cm.ReplyTo = &networkid.MessageOptionalPartID{}
			if peerID, present := replyTo.GetReplyToPeerID(); present {
				cm.ReplyTo.MessageID = ids.MakeMessageID(peerID, replyTo.ReplyToMsgID)
			} else {
				cm.ReplyTo.MessageID = ids.MakeMessageID(portal.PortalKey, replyTo.ReplyToMsgID)
			}
		default:
			log.Warn().Type("reply_to", replyTo).Msg("unhandled reply to type")
		}
	}

	if disappearingSetting == nil {
		// The TTL is either included in the message, or it's on the portal's
		// metadata.
		if ttl, ok := msg.GetTTLPeriod(); ok {
			cm.Disappear = database.DisappearingSetting{
				Type:  database.DisappearingTypeAfterSend,
				Timer: time.Duration(ttl) * time.Second,
			}
		} else if portal.Metadata.(*PortalMetadata).MessagesTTL > 0 {
			cm.Disappear = database.DisappearingSetting{
				Type:  database.DisappearingTypeAfterSend,
				Timer: time.Duration(ttl) * time.Second,
			}
		}
	}

	return
}

func (t *TelegramClient) parseBodyAndHTML(ctx context.Context, message string, entities []tg.MessageEntityClass) (*event.MessageEventContent, error) {
	if len(entities) == 0 {
		return &event.MessageEventContent{MsgType: event.MsgText, Body: message}, nil
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
		return nil, err
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

	if pc, ok := webpage.GetPhoto(); ok && pc.TypeID() == tg.PhotoTypeID {
		var fileInfo *event.FileInfo
		transferer := media.NewTransferer(c.client.API()).WithPhoto(pc)
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
	}

	return preview, nil
}

func (c *TelegramClient) convertMediaRequiringUpload(ctx context.Context, portal *bridgev2.Portal, intent bridgev2.MatrixAPI, msgID int, msgMedia tg.MessageMediaClass) (*bridgev2.ConvertedMessagePart, *database.DisappearingSetting, error) {
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

	transferer := media.NewTransferer(c.client.API()).WithRoomID(portal.MXID)
	var mediaTransferer *media.ReadyTransferer

	var disappearingSetting *database.DisappearingSetting
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
					return &bridgev2.ConvertedMessagePart{
						Type: event.EventMessage,
						Content: &event.MessageEventContent{
							MsgType: event.MsgNotice,
							Body:    fmt.Sprintf("You received a view once %s. For added privacy, you can only open it on the Telegram app.", typeName),
						},
					}, nil, nil
				}
			}

			if c.main.Config.DisableDisappearing {
				return &bridgev2.ConvertedMessagePart{
					Type: event.EventMessage,
					Content: &event.MessageEventContent{
						MsgType: event.MsgNotice,
						Body:    fmt.Sprintf("You received a disappearing %s. For added privacy, you can only open it on the Telegram app.", typeName),
					},
				}, nil, nil
			}

			disappearingSetting = &database.DisappearingSetting{
				Type:  database.DisappearingTypeAfterRead,
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
		telegramMediaID = msgMedia.Photo.GetID()
		mediaTransferer = transferer.WithPhoto(msgMedia.Photo)
	case *tg.MessageMediaDocument:
		document, ok := msgMedia.Document.(*tg.Document)
		if !ok {
			return nil, nil, fmt.Errorf("unrecognized document type %T", msgMedia.Document)
		}
		telegramMediaID = document.GetID()

		content.MsgType = event.MsgFile

		extraInfo := map[string]any{}
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
				stickerInfo := map[string]any{"alt": a.Alt, "id": document.ID}

				if setID, ok := a.Stickerset.(*tg.InputStickerSetID); ok {
					stickerInfo["pack"] = map[string]any{
						"id":          setID.ID,
						"access_hash": setID.AccessHash,
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
		extra["info"] = extraInfo

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
					log.Err(err).Msg("error getting direct download URL for thumbnail")
				}
			}
			if thumbnailURL == "" {
				thumbnailURL, thumbnailFile, thumbnailInfo, err = thumbnailTransferer.Transfer(ctx, c.main.Store, intent)
				if err != nil {
					return nil, nil, fmt.Errorf("error transferring thumbnail: %w", err)
				}
			}

			transferer = transferer.WithThumbnail(thumbnailURL, thumbnailFile, thumbnailInfo)
		}

		mediaTransferer = transferer.
			WithFilename(content.Body).
			WithDocument(msgMedia.Document, false)
	default:
		return nil, nil, fmt.Errorf("unhandled media type %T", msgMedia)
	}

	var err error
	if c.main.useDirectMedia && (!isSticker || c.main.Config.AnimatedSticker.Target == "disable") {
		content.URL, content.Info, err = mediaTransferer.DirectDownloadURL(ctx, c.telegramUserID, portal, msgID, false, telegramMediaID)
		if err != nil {
			log.Err(err).Msg("error getting direct download URL for media")
		}
	}
	if content.URL == "" {
		content.URL, content.File, content.Info, err = mediaTransferer.Transfer(ctx, c.main.Store, intent)
		if err != nil {
			return nil, nil, fmt.Errorf("error transferring media: %w", err)
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
		if extra["info"] == nil {
			extra["info"] = map[string]any{}
		}
		info := extra["info"].(map[string]any)
		info["fi.mau.telegram.spoiler"] = true
	}

	return &bridgev2.ConvertedMessagePart{
		Type:    eventType,
		Content: &content,
		Extra:   extra,
	}, disappearingSetting, nil
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

func convertLocation(media tg.MessageMediaClass) (*bridgev2.ConvertedMessagePart, error) {
	g, ok := media.(hasGeo)
	if !ok || g.GetGeo().TypeID() != tg.GeoPointTypeID {
		return nil, fmt.Errorf("location didn't have geo or geo is wrong type")
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
	}, nil
}

func convertPoll(media tg.MessageMediaClass) *bridgev2.ConvertedMessagePart {
	// TODO (PLAT-25224) make this richer in the future once megabridge has support for polls

	poll := media.(*tg.MessageMediaPoll)
	var textAnswers []string
	var htmlAnswers strings.Builder
	for i, opt := range poll.Poll.Answers {
		textAnswers = append(textAnswers, fmt.Sprintf("%d. %s", i+1, opt.Text.Text))
		htmlAnswers.WriteString(fmt.Sprintf("<li>%s</li>", opt.Text.Text))
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

func (c *TelegramClient) convertUserProfilePhoto(ctx context.Context, userID int64, photo *tg.UserProfilePhoto) (*bridgev2.Avatar, error) {
	avatar := &bridgev2.Avatar{
		ID: ids.MakeAvatarID(photo.PhotoID),
	}

	if c.main.useDirectMedia {
		mediaID, err := ids.DirectMediaInfo{
			PeerType: ids.PeerTypeUser,
			PeerID:   userID,
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
			transferer, err := media.NewTransferer(c.client.API()).WithUserPhoto(ctx, c.ScopedStore, userID, photo.PhotoID)
			if err != nil {
				return nil, err
			}
			return transferer.DownloadBytes(ctx)
		}
	}

	return avatar, nil
}

func (c *TelegramClient) convertChatPhoto(ctx context.Context, channelID, accessHash int64, chatPhoto *tg.ChatPhoto) (*bridgev2.Avatar, error) {
	avatar := &bridgev2.Avatar{
		ID: ids.MakeAvatarID(chatPhoto.PhotoID),
	}

	if c.main.useDirectMedia {
		mediaID, err := ids.DirectMediaInfo{
			PeerType: ids.PeerTypeChannel,
			PeerID:   channelID,
			UserID:   c.telegramUserID,
			ID:       chatPhoto.PhotoID,
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
			return media.NewTransferer(c.client.API()).WithChannelPhoto(channelID, accessHash, chatPhoto.PhotoID).DownloadBytes(ctx)
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
