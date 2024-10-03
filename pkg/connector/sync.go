package connector

import (
	"context"
	"fmt"
	"math"
	"time"

	"github.com/gotd/td/tg"
	"github.com/rs/zerolog"
	"go.mau.fi/util/ptr"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/bridgev2/simplevent"
	"maunium.net/go/mautrix/event"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
)

func (t *TelegramClient) SyncChats(ctx context.Context) error {
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

	if err := t.resetPinnedDialogs(ctx, dialogs.GetDialogs()); err != nil {
		return err
	}

	return t.handleDialogs(ctx, dialogs, t.main.Config.Sync.CreateLimit)
}

func (t *TelegramClient) resetPinnedDialogs(ctx context.Context, dialogs []tg.DialogClass) error {
	t.userLogin.Metadata.(*UserLoginMetadata).PinnedDialogs = nil
	for _, dialog := range dialogs {
		if dialog.GetPinned() {
			portalKey := t.makePortalKeyFromPeer(dialog.GetPeer())
			t.userLogin.Metadata.(*UserLoginMetadata).PinnedDialogs = append(t.userLogin.Metadata.(*UserLoginMetadata).PinnedDialogs, portalKey.ID)
		}
	}
	return t.userLogin.Save(ctx)
}

func (t *TelegramClient) handleDialogs(ctx context.Context, dialogs tg.ModifiedMessagesDialogs, createLimit int) error {
	log := zerolog.Ctx(ctx)

	users := map[networkid.UserID]tg.UserClass{}
	for _, user := range dialogs.GetUsers() {
		users[ids.MakeUserID(user.GetID())] = user
	}
	chats := map[int64]tg.ChatClass{}
	for _, chat := range dialogs.GetChats() {
		chats[chat.GetID()] = chat
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

		portalKey := t.makePortalKeyFromPeer(dialog.GetPeer())
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

		var members bridgev2.ChatMemberList
		switch peer := dialog.Peer.(type) {
		case *tg.PeerUser:
			if users[ids.MakeUserID(peer.UserID)].(*tg.User).GetDeleted() {
				log.Debug().Msg("Not syncing portal because user is deleted")
				continue
			}
		case *tg.PeerChat:
			members.PowerLevels = t.getGroupChatPowerLevels(chats[peer.ChatID])
		case *tg.PeerChannel:
			members.PowerLevels = t.getGroupChatPowerLevels(chats[peer.ChannelID])
			if !portal.Metadata.(*PortalMetadata).IsSuperGroup {
				// Add the channel user
				sender := ids.MakeChannelUserID(peer.ChannelID)
				members.MemberMap = map[networkid.UserID]bridgev2.ChatMember{
					sender: bridgev2.ChatMember{
						EventSender: bridgev2.EventSender{Sender: sender},
						PowerLevel:  modPowerLevel,
					},
				}
				if chats[peer.ChannelID].(*tg.Channel).AdminRights.PostMessages {
					members.MemberMap = map[networkid.UserID]bridgev2.ChatMember{
						t.userID: bridgev2.ChatMember{
							EventSender: t.mySender(),
							PowerLevel:  modPowerLevel,
						},
					}
				}
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
			if createLimit >= 0 && created > createLimit {
				break
			}
		}

		var userLocalInfo bridgev2.UserLocalPortalInfo
		if mu, ok := dialog.NotifySettings.GetMuteUntil(); ok {
			userLocalInfo.MutedUntil = ptr.Ptr(time.Unix(int64(mu), 0))
		} else {
			userLocalInfo.MutedUntil = &bridgev2.Unmuted
		}
		if dialog.Pinned {
			userLocalInfo.Tag = ptr.Ptr(event.RoomTagFavourite)
		}

		t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatResync{
			ChatInfo: &bridgev2.ChatInfo{
				Name:      &portal.Name,
				UserLocal: &userLocalInfo,
				Members:   &members,
			},
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
