package msgconv

import (
	"context"
	"fmt"
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
	conmedia "go.mau.fi/mautrix-telegram/pkg/connector/media"
)

func (mc *MessageConverter) ToMatrix(ctx context.Context, portal *bridgev2.Portal, intent bridgev2.MatrixAPI, msg *tg.Message) (*bridgev2.ConvertedMessage, error) {
	log := zerolog.Ctx(ctx).With().Str("conversion_direction", "to_matrix").Logger()
	ctx = log.WithContext(ctx)

	cm := &bridgev2.ConvertedMessage{}
	if msg.Message != "" {
		cm.Parts = append(cm.Parts, &bridgev2.ConvertedMessagePart{
			ID:      networkid.PartID("caption"),
			Type:    event.EventMessage,
			Content: &event.MessageEventContent{MsgType: event.MsgText, Body: msg.Message},
		})
	}

	if m, ok := msg.GetMedia(); ok {
		switch media := m.(type) {
		case *tg.MessageMediaPhoto:
			var filename string
			var mxcURI id.ContentURIString
			var encryptedFileInfo *event.EncryptedFileInfo
			if mc.useDirectMedia {
				var err error
				filename = "image"
				peerType, chatID, err := ids.ParsePortalID(portal.ID)
				if err != nil {
					return nil, err
				}
				mediaID, err := ids.DirectMediaInfo{
					PeerType:  peerType,
					ChatID:    chatID,
					MessageID: int64(msg.ID),
				}.AsMediaID()
				if err != nil {
					return nil, err
				}
				mxcURI, err = portal.Bridge.Matrix.GenerateContentURI(ctx, mediaID)
				if err != nil {
					return nil, err
				}
			}

			if mxcURI == "" {
				data, mimeType, err := conmedia.DownloadPhoto(ctx, mc.client.API(), media)
				if err != nil {
					return nil, err
				}
				if ttl, ok := media.GetTTLSeconds(); ok {
					filename = "disappearing_image" + exmime.ExtensionFromMimetype(mimeType)
					cm.Disappear = database.DisappearingSetting{
						Type:  database.DisappearingTypeAfterSend,
						Timer: time.Duration(ttl) * time.Second,
					}
				} else {
					filename = "image" + exmime.ExtensionFromMimetype(mimeType)
				}

				mxcURI, encryptedFileInfo, err = intent.UploadMedia(ctx, "", data, filename, mimeType)
				if err != nil {
					return nil, err
				}
			}

			extra := map[string]any{}
			if media.GetSpoiler() {
				// See: https://github.com/matrix-org/matrix-spec-proposals/pull/3725
				extra["town.robin.msc3725.content_warning"] = map[string]any{
					"type": "town.robin.msc3725.spoiler",
				}
			}

			cm.Parts = append(cm.Parts, &bridgev2.ConvertedMessagePart{
				ID:   networkid.PartID("photo"),
				Type: event.EventMessage,
				Content: &event.MessageEventContent{
					MsgType: event.MsgImage,
					Body:    filename,
					URL:     mxcURI,
					File:    encryptedFileInfo,
				},
				Extra: extra,
			})

			// TODO all of these
			// case *tg.MessageMediaGeo: // messageMediaGeo#56e0d474
			// case *tg.MessageMediaContact: // messageMediaContact#70322949
			// case *tg.MessageMediaUnsupported: // messageMediaUnsupported#9f84f49e
			// case *tg.MessageMediaDocument: // messageMediaDocument#4cf4d72d
			// case *tg.MessageMediaWebPage: // messageMediaWebPage#ddf10c3b
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
			return nil, fmt.Errorf("unhandled media type %T", m)
		}
	}
	return cm, nil
}
