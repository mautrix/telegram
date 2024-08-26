package connector

import (
	"context"
	"fmt"
	"math"

	"github.com/gotd/td/tg"
	"github.com/rs/zerolog"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/bridgev2/simplevent"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
)

func (t *TelegramClient) SyncChats(ctx context.Context) error {
	log := zerolog.Ctx(ctx)

	limit := t.main.Config.Sync.UpdateLimit
	if limit <= 0 {
		limit = math.MaxInt32
	}

	dialogs, err := APICallWithUpdates(ctx, t, func() (tg.ModifiedMessagesDialogs, error) {
		d, err := t.client.API().MessagesGetDialogs(ctx, &tg.MessagesGetDialogsRequest{
			Limit:      limit,
			OffsetPeer: &tg.InputPeerEmpty{},
		})
		if err != nil {
			return nil, err
		} else if dialogs, ok := d.(tg.ModifiedMessagesDialogs); !ok {
			return nil, fmt.Errorf("unexpected dialogs type %T", d)
		} else {
			return dialogs, nil
		}
	})
	if err != nil {
		return err
	}

	users := map[networkid.UserID]tg.UserClass{}
	for _, user := range dialogs.GetUsers() {
		users[ids.MakeUserID(user.GetID())] = user
	}
	messages := map[networkid.MessageID]tg.MessageClass{}
	for _, message := range dialogs.GetMessages() {
		messages[ids.GetMessageIDFromMessage(message)] = message
	}

	var created int
	for _, d := range dialogs.GetDialogs() {
		if d.TypeID() != tg.DialogTypeID {
			continue
		}
		dialog := d.(*tg.Dialog)

		log := log.With().
			Stringer("peer", dialog.Peer).
			Int("top_message", dialog.TopMessage).
			Logger()
		log.Debug().Msg("Syncing dialog")

		portalKey := ids.MakePortalKey(dialog.GetPeer(), t.loginID)
		portal, err := t.main.Bridge.GetPortalByKey(ctx, portalKey)
		if err != nil {
			log.Err(err).Msg("Failed to get portal")
			continue
		}

		// If this is a DM, make sure that the user isn't deleted.
		if user, ok := dialog.Peer.(*tg.PeerUser); ok {
			if users[ids.MakeUserID(user.UserID)].(*tg.User).GetDeleted() {
				log.Debug().Msg("Not syncing portal because user is deleted")
				continue
			}
		}

		if portal == nil || portal.MXID == "" {
			// Check what the latest message is
			topMessage := messages[ids.MakeMessageID(dialog.Peer, dialog.TopMessage)]
			if topMessage.TypeID() == tg.MessageServiceTypeID {
				action := topMessage.(*tg.MessageService).Action
				if action.TypeID() == tg.MessageActionContactSignUpTypeID || action.TypeID() == tg.MessageActionHistoryClearTypeID {
					log.Debug().Str("action_type", action.TypeName()).Msg("Not syncing portal because it's a contact sign up or history clear")
					continue
				}
			}

			created++ // The portal will have to be created
			if created > t.main.Config.Sync.CreateLimit {
				break
			}
		}

		t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatResync{
			EventMeta: simplevent.EventMeta{
				Type: bridgev2.RemoteEventChatResync,
				LogContext: func(c zerolog.Context) zerolog.Context {
					return c.Str("update", "sync")
				},
				PortalKey:    portalKey,
				CreatePortal: true,
			},
			CheckNeedsBackfillFunc: func(ctx context.Context, latestMessage *database.Message) (bool, error) {
				_, latestMessageID, err := ids.ParseMessageID(latestMessage.ID)
				if err != nil {
					return false, err
				}
				return dialog.TopMessage > latestMessageID, nil
			},
		})
	}
	return nil
}
