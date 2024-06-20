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

	"go.mau.fi/mautrix-telegram/pkg/connector/download"
	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/connector/util"
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
		default:
			return nil, fmt.Errorf("unsupported media type %T", media)
		}
	}
	return cm, nil
}

func (mc *MessageConverter) webpageToBeeperLinkPreview(ctx context.Context, intent bridgev2.MatrixAPI, media tg.MessageMediaClass) (*event.BeeperLinkPreview, error) {
	webpage, ok := media.(*tg.MessageMediaWebPage).Webpage.(*tg.WebPage)
	if !ok {
		return nil, nil
	}
	preview := &event.BeeperLinkPreview{
		MatchedURL: webpage.URL,
		LinkPreview: event.LinkPreview{
			Title:        webpage.Title,
			CanonicalURL: webpage.URL,
			Description:  webpage.Description,
		},
	}

	if pc, ok := webpage.GetPhoto(); ok && pc.TypeID() == tg.PhotoTypeID {
		photo := pc.(*tg.Photo)
		for _, s := range photo.GetSizes() {
			switch size := s.(type) {
			case *tg.PhotoCachedSize:
				preview.ImageWidth = size.GetW()
				preview.ImageHeight = size.GetH()
			case *tg.PhotoSizeProgressive:
				preview.ImageWidth = size.GetW()
				preview.ImageHeight = size.GetH()
			}
		}

		data, mimeType, err := download.DownloadPhoto(ctx, mc.client.API(), photo)
		if err != nil {
			return nil, err
		}
		preview.ImageSize = len(data)
		preview.ImageType = mimeType

		preview.ImageURL, preview.ImageEncryption, err = intent.UploadMedia(ctx, "", data, "", mimeType)
		if err != nil {
			return nil, err
		}
	}

	return preview, nil
}

func (mc *MessageConverter) convertMediaRequiringUpload(ctx context.Context, portal *bridgev2.Portal, intent bridgev2.MatrixAPI, msgID int, media tg.MessageMediaClass) (*bridgev2.ConvertedMessagePart, *database.DisappearingSetting, error) {
	var partID networkid.PartID
	var msgType event.MessageType
	var filename string
	var audio *event.MSC1767Audio
	var voice *event.MSC3245Voice

	// Determine the filename and some other information
	switch media := media.(type) {
	case *tg.MessageMediaPhoto:
		partID = networkid.PartID("photo")
		msgType = event.MsgImage
		filename = "image"
	case *tg.MessageMediaDocument:
		partID = networkid.PartID("document")
		msgType = event.MsgFile
		document, ok := media.Document.(*tg.Document)
		if !ok {
			return nil, nil, fmt.Errorf("unrecognized document type %T", media.Document)
		}

		for _, attr := range document.GetAttributes() {
			switch a := attr.(type) {
			case *tg.DocumentAttributeFilename:
				filename = a.GetFileName()
			case *tg.DocumentAttributeAudio:
				msgType = event.MsgAudio
				audio = &event.MSC1767Audio{
					Duration: a.Duration * 1000,
				}
				if waveform, ok := a.GetWaveform(); ok {
					for _, v := range waveform {
						audio.Waveform = append(audio.Waveform, int(v)<<5)
					}
				}
				if a.Voice {
					voice = &event.MSC3245Voice{}
				}
			}
		}

		// TODO all of these
		// case *tg.MessageMediaUnsupported: // messageMediaUnsupported#9f84f49e
		// case *tg.MessageMediaGame: // messageMediaGame#fdb19008
		// case *tg.MessageMediaInvoice: // messageMediaInvoice#f6a548d3
		// case *tg.MessageMediaPoll: // messageMediaPoll#4bd6e798
		// case *tg.MessageMediaDice: // messageMediaDice#3f7ee58b
		// case *tg.MessageMediaStory: // messageMediaStory#68cb6283
		// case *tg.MessageMediaGiveaway: // messageMediaGiveaway#daad85b0
		// case *tg.MessageMediaGiveawayResults: // messageMediaGiveawayResults#c6991068
	default:
		return nil, nil, fmt.Errorf("unhandled media type %T", media)
	}

	var mxcURI id.ContentURIString
	var encryptedFileInfo *event.EncryptedFileInfo

	if mc.useDirectMedia {
		var err error
		peerType, chatID, err := ids.ParsePortalID(portal.ID)
		if err != nil {
			return nil, nil, err
		}
		mediaID, err := ids.DirectMediaInfo{
			PeerType:  peerType,
			ChatID:    chatID,
			MessageID: int64(msgID),
		}.AsMediaID()
		if err != nil {
			return nil, nil, err
		}
		mxcURI, err = portal.Bridge.Matrix.GenerateContentURI(ctx, mediaID)
		if err != nil {
			return nil, nil, err
		}
	}

	if mxcURI == "" {
		var data []byte
		var mimeType string
		var err error
		switch media := media.(type) {
		case *tg.MessageMediaPhoto:
			if _, ok := media.GetTTLSeconds(); ok {
				filename = "disappearing_image" + exmime.ExtensionFromMimetype(mimeType)
			} else {
				filename = "image" + exmime.ExtensionFromMimetype(mimeType)
			}

			data, mimeType, err = download.DownloadPhotoMedia(ctx, mc.client.API(), media)
		case *tg.MessageMediaDocument:
			document, ok := media.Document.(*tg.Document)
			if !ok {
				return nil, nil, fmt.Errorf("unrecognized document type %T", media.Document)
			}

			mimeType = document.GetMimeType()
			data, err = download.DownloadDocument(ctx, mc.client.API(), document)

			// TODO all of these
			// case *tg.MessageMediaUnsupported: // messageMediaUnsupported#9f84f49e
			// case *tg.MessageMediaGame: // messageMediaGame#fdb19008
			// case *tg.MessageMediaInvoice: // messageMediaInvoice#f6a548d3
			// case *tg.MessageMediaPoll: // messageMediaPoll#4bd6e798
			// case *tg.MessageMediaDice: // messageMediaDice#3f7ee58b
			// case *tg.MessageMediaStory: // messageMediaStory#68cb6283
			// case *tg.MessageMediaGiveaway: // messageMediaGiveaway#daad85b0
			// case *tg.MessageMediaGiveawayResults: // messageMediaGiveawayResults#c6991068
		default:
			return nil, nil, fmt.Errorf("unhandled media type %T", media)
		}
		if err != nil {
			return nil, nil, err
		}

		mxcURI, encryptedFileInfo, err = intent.UploadMedia(ctx, "", data, filename, mimeType)
		if err != nil {
			return nil, nil, err
		}
	}

	extra := map[string]any{}

	// Handle spoilers
	// See: https://github.com/matrix-org/matrix-spec-proposals/pull/3725
	if s, ok := media.(spoilable); ok && s.GetSpoiler() {
		extra["town.robin.msc3725.content_warning"] = map[string]any{
			"type": "town.robin.msc3725.spoiler",
		}
		extra["fi.mau.telegram.spoiler"] = true
	}

	// Handle disappearing messages
	var disappearingSetting *database.DisappearingSetting
	if t, ok := media.(ttlable); ok {
		if ttl, ok := t.GetTTLSeconds(); ok {
			disappearingSetting = &database.DisappearingSetting{
				Type:  database.DisappearingTypeAfterSend,
				Timer: time.Duration(ttl) * time.Second,
			}
		}
	}

	return &bridgev2.ConvertedMessagePart{
		ID:   partID,
		Type: event.EventMessage,
		Content: &event.MessageEventContent{
			MsgType:      msgType,
			Body:         filename,
			URL:          mxcURI,
			File:         encryptedFileInfo,
			MSC1767Audio: audio,
			MSC3245Voice: voice,
		},
		Extra: extra,
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
			mc.connector.FormatGhostMXID(ids.MakeUserID(contact.UserID)),
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
		"uri":         geoURI,
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
