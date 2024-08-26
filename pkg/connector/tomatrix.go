package connector

import (
	"context"
	"crypto/sha256"
	"encoding/binary"
	"fmt"
	"html"
	"strings"
	"time"

	"github.com/gotd/td/tg"
	"github.com/rs/zerolog"
	"go.mau.fi/util/exmime"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/event"
	"maunium.net/go/mautrix/id"

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

func mediaHashID(media tg.MessageMediaClass) []byte {
	switch media := media.(type) {
	case *tg.MessageMediaPhoto:
		return binary.BigEndian.AppendUint64(nil, uint64(media.Photo.GetID()))
	case *tg.MessageMediaDocument:
		return binary.BigEndian.AppendUint64(nil, uint64(media.Document.GetID()))
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
			ID:   networkid.PartID("unsupported_media"),
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
		return converted, disappearingSetting, mediaHashID(media), err
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
			ID:   networkid.PartID("story"),
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

func (c *TelegramClient) convertToMatrix(ctx context.Context, portal *bridgev2.Portal, intent bridgev2.MatrixAPI, msg *tg.Message) (cm *bridgev2.ConvertedMessage, err error) {
	log := zerolog.Ctx(ctx).With().Str("conversion_direction", "to_matrix").Logger()
	ctx = log.WithContext(ctx)

	cm = &bridgev2.ConvertedMessage{}
	hasher := sha256.New()
	if len(msg.Message) > 0 {
		hasher.Write([]byte(msg.Message))

		content, err := c.parseBodyAndHTML(ctx, msg.Message, msg.Entities)
		if err != nil {
			return nil, err
		}
		if media, ok := msg.GetMedia(); ok && media.TypeID() == tg.MessageMediaWebPageTypeID {
			preview, err := c.webpageToBeeperLinkPreview(ctx, intent, media)
			if err != nil {
				log.Err(err).Msg("error converting webpage to link preview")
			} else if preview != nil {
				content.BeeperLinkPreviews = append(content.BeeperLinkPreviews, preview)
			}
		}

		cm.Parts = []*bridgev2.ConvertedMessagePart{
			{
				ID:      networkid.PartID("caption"),
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
	cm.Parts[0].DBMetadata = &MessageMetadata{
		ContentHash: hasher.Sum(nil),
		ContentURI:  contentURI,
	}

	if replyTo, ok := msg.GetReplyTo(); ok {
		switch replyTo := replyTo.(type) {
		case *tg.MessageReplyHeader:
			cm.ReplyTo = &networkid.MessageOptionalPartID{
				MessageID: ids.MakeMessageID(replyTo.ReplyToPeerID, replyTo.ReplyToMsgID),
			}
		default:
			log.Warn().Type("reply_to", replyTo).Msg("unhandled reply to type")
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

func (c *TelegramClient) webpageToBeeperLinkPreview(ctx context.Context, intent bridgev2.MatrixAPI, msgMedia tg.MessageMediaClass) (preview *event.BeeperLinkPreview, err error) {
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
		preview.ImageURL, preview.ImageEncryption, fileInfo, err = media.NewTransferer(c.client.API()).
			WithPhoto(pc).
			Transfer(ctx, c.main.Store, intent)
		if err != nil {
			return nil, err
		}
		preview.ImageSize, preview.ImageWidth, preview.ImageHeight = fileInfo.Size, fileInfo.Width, fileInfo.Height
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
	var partID networkid.PartID
	var content event.MessageEventContent
	var telegramMediaID int64
	var isSticker, isVideoGif bool
	extra := map[string]any{}

	transferer := media.NewTransferer(c.client.API()).WithRoomID(portal.MXID)
	var mediaTransferer *media.ReadyTransferer

	// Determine the filename and some other information
	switch msgMedia := msgMedia.(type) {
	case *tg.MessageMediaPhoto:
		partID = networkid.PartID("photo")
		content.MsgType = event.MsgImage
		content.Body = "image"
		telegramMediaID = msgMedia.Photo.GetID()
		mediaTransferer = transferer.WithPhoto(msgMedia.Photo)
	case *tg.MessageMediaDocument:
		document, ok := msgMedia.Document.(*tg.Document)
		if !ok {
			return nil, nil, fmt.Errorf("unrecognized document type %T", msgMedia.Document)
		}
		telegramMediaID = document.GetID()

		partID = networkid.PartID("document")
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
				content.MsgType = event.MsgVideo
				transferer = transferer.WithVideo(a)
			case *tg.DocumentAttributeAudio:
				content.MsgType = event.MsgAudio
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
				if c.main.Config.AnimatedSticker.Target == "webm" {
					content.MsgType = event.MsgVideo
					isVideoGif = true
					extraInfo["fi.mau.telegram.animated_sticker"] = true
				} else {
					eventType = event.EventSticker
					content.MsgType = ""
				}
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

	// Handle disappearing messages
	var disappearingSetting *database.DisappearingSetting
	if t, ok := msgMedia.(ttlable); ok {
		if ttl, ok := t.GetTTLSeconds(); ok {
			if msgMedia.TypeID() == tg.MessageMediaPhotoTypeID {
				content.Body = "disappearing_" + content.Body
			}
			disappearingSetting = &database.DisappearingSetting{
				Type:  database.DisappearingTypeAfterSend,
				Timer: time.Duration(ttl) * time.Second,
			}
		}
	}

	return &bridgev2.ConvertedMessagePart{
		ID:      partID,
		Type:    eventType,
		Content: &content,
		Extra:   extra,
	}, disappearingSetting, nil
}

func (c *TelegramClient) convertContact(media tg.MessageMediaClass) *bridgev2.ConvertedMessagePart {
	contact := media.(*tg.MessageMediaContact)
	name := util.FormatFullName(contact.FirstName, contact.LastName)
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
		ID:      networkid.PartID("contact"),
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
		ID:   networkid.PartID("location"),
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
		ID:   networkid.PartID("poll"),
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
		ID:   networkid.PartID("dice"),
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
		ID:   networkid.PartID("game"),
		Type: event.EventMessage,
		Content: &event.MessageEventContent{
			MsgType: event.MsgText,
			Body:    fmt.Sprintf("Game: %s. Open the Telegram app to play.", game.Game.Title),
		},
	}
}
