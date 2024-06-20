package connector

import (
	"context"
	"fmt"
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
	sender := message.NewSender(t.client.API())
	peer, err := ids.InputPeerForPortalID(msg.Portal.ID)
	if err != nil {
		return nil, err
	}
	builder := sender.To(peer)

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
	default:
		return nil, fmt.Errorf("unsupported message type %s", msg.Content.MsgType)
	}
	if err != nil {
		return nil, err
	}

	var tgMessageID, tgDate int
	switch sentMessage := updates.(type) {
	case *tg.UpdateShortSentMessage:
		tgMessageID = sentMessage.ID
		tgDate = sentMessage.Date
	case *tg.Updates:
		tgDate = sentMessage.Date
		for _, u := range sentMessage.Updates {
			if update, ok := u.(*tg.UpdateMessageID); ok {
				tgMessageID = update.ID
				break
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
			SenderID:  ids.MakeUserID(t.loginID),
			Timestamp: time.Unix(int64(tgDate), 0),
		},
	}
	return
}

func (t *TelegramClient) HandleMatrixEdit(ctx context.Context, msg *bridgev2.MatrixEdit) error {
	panic("unimplemented edit")
}

func (t *TelegramClient) HandleMatrixMessageRemove(ctx context.Context, msg *bridgev2.MatrixMessageRemove) error {
	panic("unimplemented remove")
}

func (t *TelegramClient) PreHandleMatrixReaction(ctx context.Context, msg *bridgev2.MatrixReaction) (bridgev2.MatrixReactionPreResponse, error) {
	panic("pre handle matrix reaction")
}

func (t *TelegramClient) HandleMatrixReaction(ctx context.Context, msg *bridgev2.MatrixReaction) (reaction *database.Reaction, err error) {
	panic("unimplemented reaction")
}

func (t *TelegramClient) HandleMatrixReactionRemove(ctx context.Context, msg *bridgev2.MatrixReactionRemove) error {
	panic("unimplemented reaction remove")
}

func (t *TelegramClient) HandleMatrixReadReceipt(ctx context.Context, msg *bridgev2.MatrixReadReceipt) error {
	// TODO
	return nil
}

func (t *TelegramClient) HandleMatrixTyping(ctx context.Context, msg *bridgev2.MatrixTyping) error {
	// TODO
	return nil
}
