package msgconv

import (
	"context"
	"fmt"
	"slices"
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
)

type spoilable interface {
	GetSpoiler() bool
}

type ttlable interface {
	GetTTLSeconds() (value int, ok bool)
}

func mediaRequiringUpload(media tg.MessageMediaClass) bool {
	allowed := []uint32{
		tg.MessageMediaPhotoTypeID,
		tg.MessageMediaGeoTypeID,
		tg.MessageMediaContactTypeID,
		tg.MessageMediaDocumentTypeID,
		tg.MessageMediaStoryTypeID,
	}
	return slices.Contains(allowed, media.TypeID())
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
		switch {
		case mediaRequiringUpload(media):
			mediaParts, disappearingSetting, err := mc.convertMediaRequiringUpload(ctx, portal, intent, msg.ID, media)
			if err != nil {
				return nil, err
			}
			if disappearingSetting != nil {
				cm.Disappear = *disappearingSetting
			}
			cm.Parts = append(cm.Parts, mediaParts)
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
		// case *tg.MessageMediaGeo: // messageMediaGeo#56e0d474
		// case *tg.MessageMediaContact: // messageMediaContact#70322949
		// case *tg.MessageMediaUnsupported: // messageMediaUnsupported#9f84f49e
		// case *tg.MessageMediaVenue: // messageMediaVenue#2ec0533f
		// case *tg.MessageMediaGame: // messageMediaGame#fdb19008
		// case *tg.MessageMediaInvoice: // messageMediaInvoice#f6a548d3
		// case *tg.MessageMediaGeoLive: // messageMediaGeoLive#b940c666
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
			// case *tg.MessageMediaGeo: // messageMediaGeo#56e0d474
			// case *tg.MessageMediaContact: // messageMediaContact#70322949
			// case *tg.MessageMediaUnsupported: // messageMediaUnsupported#9f84f49e
			// case *tg.MessageMediaVenue: // messageMediaVenue#2ec0533f
			// case *tg.MessageMediaGame: // messageMediaGame#fdb19008
			// case *tg.MessageMediaInvoice: // messageMediaInvoice#f6a548d3
			// case *tg.MessageMediaGeoLive: // messageMediaGeoLive#b940c666
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
