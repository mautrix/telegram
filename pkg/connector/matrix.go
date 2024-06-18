package connector

import (
	"context"
	"fmt"
	"time"

	"github.com/gotd/td/telegram/message"
	"github.com/gotd/td/telegram/message/html"
	"github.com/gotd/td/telegram/uploader"
	"github.com/gotd/td/tg"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/event"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
)

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
		updates, err = builder.Text(ctx, msg.Content.Body)
		if err != nil {
			return nil, err
		}
	case event.MsgImage, event.MsgFile, event.MsgAudio, event.MsgVideo:
		var filename, caption string
		if msg.Content.FileName != "" {
			filename = msg.Content.FileName
			caption = msg.Content.FormattedBody
			if caption == "" {
				caption = msg.Content.Body
			}
		} else {
			filename = msg.Content.Body
		}

		// TODO stream this download straight into the uploader
		fileData, err := t.main.Bridge.Bot.DownloadMedia(ctx, msg.Content.URL, msg.Content.File)
		if err != nil {
			return nil, fmt.Errorf("failed to download media from Matrix: %w", err)
		}
		uploader := uploader.NewUploader(t.client.API())
		upload, err := uploader.FromBytes(ctx, filename, fileData)
		if err != nil {
			return nil, fmt.Errorf("failed to upload media to Telegram: %w", err)
		}
		var photo *message.UploadedPhotoBuilder
		if caption != "" {
			// TODO resolver?
			photo = message.UploadedPhoto(upload, html.String(nil, caption))
		} else {
			photo = message.UploadedPhoto(upload)
		}
		updates, err = builder.Media(ctx, photo)
		if err != nil {
			return nil, err
		}
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
