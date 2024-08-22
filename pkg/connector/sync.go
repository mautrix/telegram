package connector

import (
	"context"
	"fmt"
	"math"

	"github.com/gotd/td/tg"
	"github.com/rs/zerolog"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
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

		portalKey := ids.MakePortalKey(dialog.GetPeer(), t.loginID)
		portal, err := t.main.Bridge.GetPortalByKey(ctx, portalKey)
		if err != nil {
			log.Err(err).Msg("Failed to get portal")
			continue
		}

		// TODO make sure that the user isn't deleted.

		if portal == nil || portal.MXID == "" {
			// Check what the latest message is
			messages, err := APICallWithUpdates(ctx, t, func() (tg.ModifiedMessagesMessages, error) {
				inputMessages := []tg.InputMessageClass{
					&tg.InputMessageID{ID: dialog.TopMessage},
				}
				var msgs tg.MessagesMessagesClass
				switch v := dialog.Peer.(type) {
				case *tg.PeerUser, *tg.PeerChat:
					msgs, err = t.client.API().MessagesGetMessages(ctx, inputMessages)
				case *tg.PeerChannel:
					var accessHash int64
					var found bool
					accessHash, found, err = t.ScopedStore.GetChannelAccessHash(ctx, t.telegramUserID, v.ChannelID)
					if err != nil {
						return nil, fmt.Errorf("failed to get channel access hash: %w", err)
					} else if !found {
						return nil, fmt.Errorf("channel access hash for %d not found", v.ChannelID)
					} else {
						msgs, err = t.client.API().ChannelsGetMessages(ctx, &tg.ChannelsGetMessagesRequest{
							Channel: &tg.InputChannel{ChannelID: v.ChannelID, AccessHash: accessHash},
							ID:      inputMessages,
						})
					}
				default:
					return nil, fmt.Errorf("unknown peer type %T", dialog.Peer)
				}
				if err != nil {
					return nil, err
				} else if messages, ok := msgs.(tg.ModifiedMessagesMessages); !ok {
					return nil, fmt.Errorf("unsupported messages type %T", messages)
				} else {
					return messages, nil
				}
			})
			if err != nil {
				log.Err(err).Msg("Failed to get latest message for portal")
				continue
			} else if len(messages.GetMessages()) == 0 {
				log.Warn().Msg("No messages found for portal")
				continue
			}
			topMessage := messages.GetMessages()[0]
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

		// TODO use the bundled backfill data?
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
				latestMessageID, err := ids.ParseMessageID(latestMessage.ID)
				if err != nil {
					return false, err
				}
				return dialog.TopMessage > latestMessageID, nil
			},
		})
	}
	return nil
}
