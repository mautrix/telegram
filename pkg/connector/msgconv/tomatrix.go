package msgconv

import (
	"context"
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
	"go.mau.fi/mautrix-telegram/pkg/connector/util"
	"go.mau.fi/mautrix-telegram/pkg/connector/waveform"
)

type spoilable interface {
	GetSpoiler() bool
}

type ttlable interface {
	GetTTLSeconds() (value int, ok bool)
}

func (mc *MessageConverter) ToMatrix(ctx context.Context, portal *bridgev2.Portal, intent bridgev2.MatrixAPI, msg *tg.Message) (*bridgev2.ConvertedMessage, error) {
	log := zerolog.Ctx(ctx).With().Str("conversion_direction", "to_matrix").Logger()
	ctx = log.WithContext(ctx)

	cm := &bridgev2.ConvertedMessage{}
	if len(msg.Message) > 0 {
		var linkPreviews []*event.BeeperLinkPreview
		if media, ok := msg.GetMedia(); ok && media.TypeID() == tg.MessageMediaWebPageTypeID {
			preview, err := mc.webpageToBeeperLinkPreview(ctx, intent, media)
			if err != nil {
				return nil, err
			} else if preview != nil {
				linkPreviews = append(linkPreviews, preview)
			}
		}

		// TODO formatting
		// TODO combine with other media
		cm.Parts = append(cm.Parts, &bridgev2.ConvertedMessagePart{
			ID:   networkid.PartID("caption"),
			Type: event.EventMessage,
			Content: &event.MessageEventContent{
				MsgType:            event.MsgText,
				Body:               msg.Message,
				BeeperLinkPreviews: linkPreviews,
			},
		})
	}

	if media, ok := msg.GetMedia(); ok {
		switch media.TypeID() {
		case tg.MessageMediaWebPageTypeID:
			// Already handled above
		case tg.MessageMediaUnsupportedTypeID:
			cm.Parts = append(cm.Parts, &bridgev2.ConvertedMessagePart{
				ID:   networkid.PartID("unsupported_media"),
				Type: event.EventMessage,
				Content: &event.MessageEventContent{
					MsgType: event.MsgNotice,
					Body:    "This message is not supported on your version of Mautrix-Telegram. Please check https://github.com/mautrix/telegram or ask your bridge administrator about possible updates.",
				},
				Extra: map[string]any{
					"fi.mau.telegram.unsupported": true,
				},
			})
		case tg.MessageMediaPhotoTypeID, tg.MessageMediaDocumentTypeID:
			mediaPart, disappearingSetting, err := mc.convertMediaRequiringUpload(ctx, portal, intent, msg.ID, media)
			if err != nil {
				return nil, err
			}
			if disappearingSetting != nil {
				cm.Disappear = *disappearingSetting
			}
			cm.Parts = append(cm.Parts, mediaPart)
		case tg.MessageMediaContactTypeID:
			cm.Parts = append(cm.Parts, mc.convertContact(media))
		case tg.MessageMediaGeoTypeID, tg.MessageMediaGeoLiveTypeID, tg.MessageMediaVenueTypeID:
			location, err := mc.convertLocation(media)
			if err != nil {
				return nil, err
			}
			cm.Parts = append(cm.Parts, location)
		case tg.MessageMediaPollTypeID:
			cm.Parts = append(cm.Parts, mc.convertPoll(media))
		case tg.MessageMediaDiceTypeID:
			cm.Parts = append(cm.Parts, mc.convertDice(media))
		case tg.MessageMediaGameTypeID:
			cm.Parts = append(cm.Parts, mc.convertGame(media))

		case tg.MessageMediaStoryTypeID, tg.MessageMediaInvoiceTypeID, tg.MessageMediaGiveawayTypeID, tg.MessageMediaGiveawayResultsTypeID:
			// TODO: support these properly
			cm.Parts = append(cm.Parts, &bridgev2.ConvertedMessagePart{
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
			})
		default:
			return nil, fmt.Errorf("unsupported media type %T", media)
		}
	}
	return cm, nil
}

func (mc *MessageConverter) webpageToBeeperLinkPreview(ctx context.Context, intent bridgev2.MatrixAPI, msgMedia tg.MessageMediaClass) (preview *event.BeeperLinkPreview, err error) {
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
		preview.ImageURL, preview.ImageEncryption, fileInfo, err = media.NewTransferer(mc.client.API()).
			WithPhoto(pc).
			Transfer(ctx, mc.store, intent)
		if err != nil {
			return nil, err
		}
		preview.ImageSize, preview.ImageWidth, preview.ImageHeight = fileInfo.Size, fileInfo.Width, fileInfo.Height
	}

	return preview, nil
}

func (mc *MessageConverter) convertMediaRequiringUpload(ctx context.Context, portal *bridgev2.Portal, intent bridgev2.MatrixAPI, msgID int, msgMedia tg.MessageMediaClass) (*bridgev2.ConvertedMessagePart, *database.DisappearingSetting, error) {
	log := zerolog.Ctx(ctx).With().
		Str("conversion_direction", "to_matrix").
		Str("portal_id", string(portal.ID)).
		Int("msg_id", msgID).
		Logger()
	var partID networkid.PartID
	var content event.MessageEventContent

	transferer := media.NewTransferer(mc.client.API()).WithRoomID(portal.MXID)
	var mediaTransferer *media.ReadyTransferer

	// Determine the filename and some other information
	switch msgMedia := msgMedia.(type) {
	case *tg.MessageMediaPhoto:
		partID = networkid.PartID("photo")
		content.MsgType = event.MsgImage
		content.Body = "image"
		mediaTransferer = transferer.WithPhoto(msgMedia.Photo)
	case *tg.MessageMediaDocument:
		document, ok := msgMedia.Document.(*tg.Document)
		if !ok {
			return nil, nil, fmt.Errorf("unrecognized document type %T", msgMedia.Document)
		}

		partID = networkid.PartID("document")
		content.MsgType = event.MsgFile

		if _, ok := document.GetThumbs(); ok {
			var thumbnailURL id.ContentURIString
			var thumbnailFile *event.EncryptedFileInfo
			var thumbnailInfo *event.FileInfo
			var err error

			thumbnailTransferer := media.NewTransferer(mc.client.API()).
				WithRoomID(portal.MXID).
				WithDocument(document, true)
			if mc.useDirectMedia {
				thumbnailURL, thumbnailInfo, err = thumbnailTransferer.DirectDownloadURL(ctx, portal, msgID, true)
				if err != nil {
					log.Err(err).Msg("error getting direct download URL for thumbnail")
				}
			}
			if thumbnailURL == "" {
				thumbnailURL, thumbnailFile, thumbnailInfo, err = thumbnailTransferer.Transfer(ctx, mc.store, intent)
				if err != nil {
					return nil, nil, fmt.Errorf("error transferring thumbnail: %w", err)
				}
			}

			transferer = transferer.WithThumbnail(thumbnailURL, thumbnailFile, thumbnailInfo)
		}

		for _, attr := range document.GetAttributes() {
			switch a := attr.(type) {
			case *tg.DocumentAttributeFilename:
				content.Body = a.GetFileName()
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
			}
		}

		mediaTransferer = transferer.
			WithFilename(content.Body).
			WithDocument(msgMedia.Document, false)
	default:
		return nil, nil, fmt.Errorf("unhandled media type %T", msgMedia)
	}

	var err error
	if mc.useDirectMedia {
		content.URL, content.Info, err = mediaTransferer.DirectDownloadURL(ctx, portal, msgID, false)
		if err != nil {
			log.Err(err).Msg("error getting direct download URL for media")
		}
	}
	if content.URL == "" {
		content.URL, content.File, content.Info, err = mediaTransferer.Transfer(ctx, mc.store, intent)
		if err != nil {
			return nil, nil, fmt.Errorf("error transferring media: %w", err)
		}
		if msgMedia.TypeID() == tg.MessageMediaPhotoTypeID {
			content.Body = content.Body + exmime.ExtensionFromMimetype(content.Info.MimeType)
		}
	}

	extra := map[string]any{}

	// Handle spoilers
	// See: https://github.com/matrix-org/matrix-spec-proposals/pull/3725
	if s, ok := msgMedia.(spoilable); ok && s.GetSpoiler() {
		extra["town.robin.msc3725.content_warning"] = map[string]any{
			"type": "town.robin.msc3725.spoiler",
		}
		extra["fi.mau.telegram.spoiler"] = true
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
		Type:    event.EventMessage,
		Content: &content,
		Extra:   extra,
	}, disappearingSetting, nil
}

func (mc *MessageConverter) convertContact(media tg.MessageMediaClass) *bridgev2.ConvertedMessagePart {
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
			`Shared contact info for <a href="https://matrix.to/#/%s">%s</a>: %s`,
			mc.connector.GhostIntent(ids.MakeUserID(contact.UserID)).GetMXID(),
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

func (mc *MessageConverter) convertLocation(media tg.MessageMediaClass) (*bridgev2.ConvertedMessagePart, error) {
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
	geoURI := GeoURIFromLatLong(point.Lat, point.Long)
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
		"uri":         geoURI.URI(),
		"description": note,
	}

	return &bridgev2.ConvertedMessagePart{
		ID:   networkid.PartID("location"),
		Type: event.EventMessage,
		Content: &event.MessageEventContent{
			MsgType:       event.MsgLocation,
			GeoURI:        geoURI.URI(),
			Body:          fmt.Sprintf("%s: %s\n%s", note, body, url),
			Format:        event.FormatHTML,
			FormattedBody: fmt.Sprintf(`%s: <a href="%s">%s</a>`, note, url, body),
		},
		Extra: extra,
	}, nil
}

func (mc *MessageConverter) convertPoll(media tg.MessageMediaClass) *bridgev2.ConvertedMessagePart {
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

func (mc *MessageConverter) convertDice(media tg.MessageMediaClass) *bridgev2.ConvertedMessagePart {
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

func (mc *MessageConverter) convertGame(media tg.MessageMediaClass) *bridgev2.ConvertedMessagePart {
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
