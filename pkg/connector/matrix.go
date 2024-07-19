package connector

import (
	"context"
	"crypto/sha256"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/gotd/td/telegram/message"
	"github.com/gotd/td/telegram/message/html"
	"github.com/gotd/td/telegram/message/styling"
	"github.com/gotd/td/telegram/uploader"
	"github.com/gotd/td/tg"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/event"

	"go.mau.fi/util/variationselector"

	"go.mau.fi/mautrix-telegram/pkg/connector/emojis"
	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/connector/waveform"
)

func getMediaFilenameAndCaption(content *event.MessageEventContent) (filename, caption string) {
	if content.FileName != "" {
		filename = content.FileName
		caption = content.FormattedBody
		if caption == "" {
			caption = content.Body
		}
	} else {
		filename = content.Body
	}
	return
}

func (t *TelegramClient) HandleMatrixMessage(ctx context.Context, msg *bridgev2.MatrixMessage) (resp *bridgev2.MatrixMessageResponse, err error) {
	peer, err := t.inputPeerForPortalID(ctx, msg.Portal.ID)
	if err != nil {
		return nil, err
	}
	builder := message.NewSender(t.client.API()).To(peer)

	// TODO handle sticker

	var updates tg.UpdatesClass
	switch msg.Content.MsgType {
	case event.MsgText:
		if msg.Content.BeeperLinkPreviews != nil && len(msg.Content.BeeperLinkPreviews) == 0 {
			builder.NoWebpage()
		}
		updates, err = builder.Text(ctx, msg.Content.Body)
	case event.MsgImage, event.MsgFile, event.MsgAudio, event.MsgVideo:
		filename, caption := getMediaFilenameAndCaption(msg.Content)

		var fileData []byte
		fileData, err = t.main.Bridge.Bot.DownloadMedia(ctx, msg.Content.URL, msg.Content.File)
		if err != nil {
			return nil, fmt.Errorf("failed to download media from Matrix: %w", err)
		}
		uploader := uploader.NewUploader(t.client.API())
		var upload tg.InputFileClass
		upload, err = uploader.FromBytes(ctx, filename, fileData)
		if err != nil {
			return nil, fmt.Errorf("failed to upload media to Telegram: %w", err)
		}
		var styling []styling.StyledTextOption
		if caption != "" {
			// TODO resolver?
			// TODO HTML
			styling = append(styling, html.String(nil, caption))
		}

		if msg.Content.MsgType == event.MsgImage {
			updates, err = builder.Media(ctx, message.UploadedPhoto(upload, styling...))
			break
		} else {
			document := message.UploadedDocument(upload, styling...).Filename(filename)
			if msg.Content.Info != nil {
				document.MIME(msg.Content.Info.MimeType)
			}

			var media message.MediaOption

			switch msg.Content.MsgType {
			case event.MsgAudio:
				audioBuilder := document.Audio()
				if msg.Content.MSC1767Audio != nil {
					audioBuilder.Duration(time.Duration(msg.Content.MSC1767Audio.Duration) * time.Millisecond)
					if len(msg.Content.MSC1767Audio.Waveform) > 0 {
						audioBuilder.Waveform(waveform.Encode(msg.Content.MSC1767Audio.Waveform))
					}
				}
				if msg.Content.MSC3245Voice != nil {
					audioBuilder.Voice()
				}
				media = audioBuilder
			default:
				media = document
			}
			updates, err = builder.Media(ctx, media)
		}
	case event.MsgLocation:
		var uri GeoURI
		uri, err = ParseGeoURI(msg.Content.GeoURI)
		if err != nil {
			return nil, err
		}
		var styling []styling.StyledTextOption
		if location, ok := msg.Event.Content.Raw["org.matrix.msc3488.location"].(map[string]any); ok {
			if desc, ok := location["description"].(string); ok {
				// TODO resolver?
				// TODO HTML
				styling = append(styling, html.String(nil, desc))
			}
		}
		updates, err = builder.Media(ctx, message.Media(&tg.InputMediaGeoPoint{
			GeoPoint: &tg.InputGeoPoint{
				Lat:  uri.Lat,
				Long: uri.Long,
			},
		}, styling...))
	default:
		return nil, fmt.Errorf("unsupported message type %s", msg.Content.MsgType)
	}
	if err != nil {
		return nil, err
	}

	hasher := sha256.New()
	hasher.Write([]byte(msg.Content.Body))

	var tgMessageID, tgDate int
	switch sentMessage := updates.(type) {
	case *tg.UpdateShortSentMessage:
		tgMessageID = sentMessage.ID
		tgDate = sentMessage.Date
	case *tg.Updates:
		tgDate = sentMessage.Date
		for _, u := range sentMessage.Updates {
			switch update := u.(type) {
			case *tg.UpdateMessageID:
				tgMessageID = update.ID
			case *tg.UpdateNewMessage:
				msg := update.Message.(*tg.Message)
				hasher.Write(mediaHashID(msg.Media))
			}
		}
		if tgMessageID == 0 {
			return nil, fmt.Errorf("couldn't find update message ID update")
		}
	default:
		return nil, fmt.Errorf("unknown update from message response %T", updates)
	}

	resp = &bridgev2.MatrixMessageResponse{
		DB: &database.Message{
			ID:        ids.MakeMessageID(tgMessageID),
			MXID:      msg.Event.ID,
			Room:      networkid.PortalKey{ID: msg.Portal.ID},
			SenderID:  t.userID,
			Timestamp: time.Unix(int64(tgDate), 0),
			Metadata:  &MessageMetadata{ContentHash: hasher.Sum(nil)},
		},
	}
	return
}

func (t *TelegramClient) HandleMatrixEdit(ctx context.Context, msg *bridgev2.MatrixEdit) error {
	panic("unimplemented edit")
}

func (t *TelegramClient) HandleMatrixMessageRemove(ctx context.Context, msg *bridgev2.MatrixMessageRemove) error {
	if dbMsg, err := t.main.Bridge.DB.Message.GetPartByMXID(ctx, msg.TargetMessage.MXID); err != nil {
		return err
	} else if messageID, err := ids.ParseMessageID(dbMsg.ID); err != nil {
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
	var resp bridgev2.MatrixReactionPreResponse

	var maxReactions int
	maxReactions, err := t.getReactionLimit(ctx, t.userID)
	if err != nil {
		return resp, err
	}

	var emojiID networkid.EmojiID
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
	} else if documentID, ok := emojis.GetEmojiDocumentID(msg.Content.RelatesTo.Key); ok {
		emojiID = ids.MakeEmojiIDFromDocumentID(documentID)
	} else {
		emojiID = ids.MakeEmojiIDFromEmoticon(msg.Content.RelatesTo.Key)
	}

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
	targetMessageID, err := ids.ParseMessageID(msg.TargetMessage.ID)
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
		existing, err := t.main.Bridge.DB.Reaction.GetAllToMessageBySender(ctx, msg.TargetReaction.MessageID, msg.TargetReaction.SenderID)
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

	messageID, err := ids.ParseMessageID(msg.TargetReaction.MessageID)
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
	// TODO
	return nil
}

func (t *TelegramClient) HandleMatrixTyping(ctx context.Context, msg *bridgev2.MatrixTyping) error {
	// TODO
	return nil
}
