// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2025 Sumner Evans
// Copyright (C) 2026 Tulir Asokan
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

package connector

import (
	"context"
	"fmt"

	"github.com/rs/zerolog"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/bridgev2/simplevent"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/gotd/bin"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tgerr"
)

func (t *TelegramClient) syncChats(ctx context.Context, takeoutID int64, onLogin, restart bool) error {
	if takeoutID != 0 && !t.main.Config.Takeout.DialogSync {
		return nil
	}
	logWith := zerolog.Ctx(ctx).With().Str("loop", "chat sync")
	if onLogin {
		logWith = logWith.Bool("on_login", true)
	}
	if takeoutID != 0 {
		logWith = logWith.Int64("takeout_id", takeoutID)
	}
	log := logWith.Logger()

	if !t.syncChatsLock.TryLock() {
		log.Warn().Msg("Waiting for chat sync lock")
		t.syncChatsLock.Lock()
		log.Debug().Msg("Acquired chat sync lock after waiting")
	}
	defer t.syncChatsLock.Unlock()

	if restart {
		t.metadata.DialogSyncCount = 0
		t.metadata.DialogSyncComplete = false
		t.metadata.DialogSyncCursor = ""
	} else if t.metadata.DialogSyncComplete {
		log.Debug().Msg("Dialogs already synced")
		return nil
	}

	isFullSync := true
	updateLimit := subtractLimit(t.main.Config.Sync.UpdateLimit, t.metadata.DialogSyncCount)
	if onLogin && t.main.Config.Takeout.DialogSync {
		updateLimit = t.main.Config.Sync.LoginLimit
		isFullSync = false
	}
	createLimit := subtractLimit(t.main.Config.Sync.CreateLimit, t.metadata.DialogSyncCount)

	var req tg.MessagesGetDialogsRequest
	isFirst := true
	if t.metadata.DialogSyncCursor != "" {
		isFirst = false
		var err error
		req.OffsetPeer, _, err = t.inputPeerForPortalID(ctx, t.metadata.DialogSyncCursor)
		if err != nil {
			return fmt.Errorf("failed to get input peer for pagination: %w", err)
		}
	} else {
		req.OffsetPeer = &tg.InputPeerEmpty{}
	}
	var wrappedReq bin.Object
	if takeoutID != 0 {
		wrappedReq = &tg.InvokeWithTakeoutRequest{TakeoutID: takeoutID, Query: &req}
	} else {
		wrappedReq = &req
	}
	for updateLimit < 0 || updateLimit > 0 {
		if updateLimit < 0 {
			req.Limit = 100
		} else {
			req.Limit = min(100, updateLimit)
		}
		log.Info().
			Stringer("request", &req).
			Int("update_limit", updateLimit).
			Int("create_limit", createLimit).
			Msg("Fetching dialogs")
		dialogs, err := APICallWithUpdates(ctx, t, func() (tg.ModifiedMessagesDialogs, error) {
			var dialogs tg.MessagesDialogsBox
			retry := true
			var err error
			for retry {
				retry, err = tgerr.FloodWait(ctx, t.client.Invoke(ctx, wrappedReq, &dialogs))
			}
			if err != nil {
				return nil, err
			} else if modified, ok := dialogs.Dialogs.AsModified(); !ok {
				return nil, fmt.Errorf("unexpected response type: %T", dialogs.Dialogs)
			} else {
				return modified, nil
			}
		})
		if err != nil {
			return fmt.Errorf("failed to get dialogs: %w", err)
		} else if len(dialogs.GetDialogs()) == 0 {
			log.Debug().Msg("No more dialogs found (empty response)")
			break
		}

		if isFirst {
			// This is the first fetch of dialogs, reset the pinned dialogs based on the list.
			if err = t.resetPinnedDialogs(ctx, dialogs.GetDialogs()); err != nil {
				return fmt.Errorf("failed to save pinned dialogs: %w", err)
			}
		}
		isFirst = false

		dialogList := dialogs.GetDialogs()
		if updateLimit > 0 && len(dialogList) > updateLimit {
			dialogList = dialogList[:updateLimit]
		}
		err = t.handleDialogs(ctx, dialogList, dialogs, createLimit)
		if err != nil {
			return fmt.Errorf("failed to handle dialogs: %w", err)
		}
		updateLimit = subtractLimit(updateLimit, len(dialogList))
		createLimit = subtractLimit(createLimit, len(dialogList))

		cursorPortalKey := t.makePortalKeyFromPeer(dialogList[len(dialogList)-1].GetPeer(), 0)
		if t.metadata.DialogSyncCursor == cursorPortalKey.ID {
			log.Debug().Msg("No more dialogs found (last dialog is same as old cursor)")
			break
		}
		t.metadata.DialogSyncCursor = cursorPortalKey.ID
		t.metadata.DialogSyncCount += len(dialogList)
		if err = t.userLogin.Save(ctx); err != nil {
			return fmt.Errorf("failed to save user login to update cursor: %w", err)
		}

		req.OffsetPeer, _, err = t.inputPeerForPortalID(ctx, cursorPortalKey.ID)
		if err != nil {
			return fmt.Errorf("failed to get input peer for pagination: %w", err)
		}
	}
	if isFullSync {
		t.metadata.DialogSyncComplete = true
		t.metadata.DialogSyncCursor = ""
		t.metadata.DialogSyncCount = 0
		if err := t.userLogin.Save(ctx); err != nil {
			return fmt.Errorf("failed to save user login after successful sync: %w", err)
		}
	}
	log.Info().Msg("Finished dialog sync")
	return nil
}

func subtractLimit(limit, count int) int {
	if limit < 0 {
		return limit
	}
	limit -= count
	if limit < 0 {
		return 0
	}
	return limit
}

func (t *TelegramClient) resetPinnedDialogs(ctx context.Context, dialogs []tg.DialogClass) error {
	t.metadata.PinnedDialogs = nil
	for _, dialog := range dialogs {
		if dialog.GetPinned() {
			portalKey := t.makePortalKeyFromPeer(dialog.GetPeer(), 0)
			t.metadata.PinnedDialogs = append(t.metadata.PinnedDialogs, portalKey.ID)
		}
	}
	return t.userLogin.Save(ctx)
}

func (t *TelegramClient) handleDialogs(ctx context.Context, dialogList []tg.DialogClass, meta tg.ModifiedMessagesDialogs, createLimit int) error {
	log := zerolog.Ctx(ctx)

	users := map[int64]tg.UserClass{}
	for _, user := range meta.GetUsers() {
		users[user.GetID()] = user
	}
	chats := map[int64]tg.ChatClass{}
	for _, chat := range meta.GetChats() {
		chats[chat.GetID()] = chat
	}
	messages := map[networkid.MessageID]tg.MessageClass{}
	for _, message := range meta.GetMessages() {
		messages[ids.GetMessageIDFromMessage(message)] = message
	}

	for i, d := range dialogList {
		dialog, ok := d.(*tg.Dialog)
		if !ok {
			continue
		}

		log := log.With().
			Stringer("peer", dialog.Peer).
			Int("top_message", dialog.TopMessage).
			Logger()
		log.Debug().Msg("Syncing dialog")

		portalKey := t.makePortalKeyFromPeer(dialog.GetPeer(), 0)
		portal, err := t.main.Bridge.GetPortalByKey(ctx, portalKey)
		if err != nil {
			return err
		}
		if dialog.UnreadCount == 0 && !dialog.UnreadMark {
			portal.Metadata.(*PortalMetadata).ReadUpTo = dialog.TopMessage
		}

		var chatInfo *bridgev2.ChatInfo
		switch peer := dialog.Peer.(type) {
		case *tg.PeerUser:
			switch user := users[peer.UserID].(type) {
			case *tg.User:
				if user.GetDeleted() {
					log.Debug().Int64("user_id", peer.UserID).Msg("Not syncing portal because user is deleted")
					continue
				}
				chatInfo, err = t.getDMChatInfo(ctx, peer.UserID)
				if err != nil {
					return fmt.Errorf("failed to get dm info for %d: %w", peer.UserID, err)
				}
			default:
				log.Debug().
					Int64("user_id", peer.UserID).
					Type("user_type", user).
					Msg("Not syncing portal because user type is unsupported")
				continue
			}
		case *tg.PeerChat:
			switch chat := chats[peer.ChatID].(type) {
			case *tg.Chat:
				// Need to get full chat info to get the member list
				chatInfo, err = t.GetChatInfo(ctx, portal)
				if err != nil {
					return fmt.Errorf("failed to get chat info for %s: %w", portalKey, err)
				}
			case *tg.ChatForbidden:
				log.Debug().
					Int64("chat_id", peer.ChatID).
					Msg("Not syncing portal because chat is forbidden")
				continue
			default:
				log.Debug().
					Int64("chat_id", peer.ChatID).
					Type("chat_type", chat).
					Msg("Not syncing portal because chat type is unsupported")
				continue
			}
		case *tg.PeerChannel:
			switch channel := chats[peer.ChannelID].(type) {
			case *tg.Channel:
				var mfm *memberFetchMeta
				chatInfo, mfm, err = t.wrapChatInfo(portal.ID, channel)
				if err != nil {
					return fmt.Errorf("failed to get chat info for %s: %w", portalKey, err)
				}
				err = t.fillChannelMembers(ctx, mfm, chatInfo.Members)
				if err != nil {
					log.Err(err).Msg("Failed to get channel members")
				}
			case *tg.ChannelForbidden:
				log.Debug().
					Int64("channel_id", peer.ChannelID).
					Msg("Not syncing portal because channel is forbidden")
				continue
			default:
				log.Debug().
					Int64("channel_id", peer.ChannelID).
					Type("channel_type", channel).
					Msg("Not syncing portal because channel type is unsupported")
				continue
			}
		}

		if portal.MXID == "" {
			// Check what the latest message is
			topMessage := messages[ids.MakeMessageID(dialog.Peer, dialog.TopMessage)]
			if topMessage == nil {
				if dialog.TopMessage == 0 {
					log.Debug().Msg("Not syncing portal because there are no messages")
					continue
				}
				log.Warn().Msg("TopMessage of dialog not in messages map")
			} else if topMessage.TypeID() == tg.MessageServiceTypeID {
				action := topMessage.(*tg.MessageService).Action
				if action.TypeID() == tg.MessageActionContactSignUpTypeID || action.TypeID() == tg.MessageActionHistoryClearTypeID {
					log.Debug().Str("action_type", action.TypeName()).Msg("Not syncing portal because it's a contact sign up or history clear")
					continue
				}
			}

			if createLimit >= 0 && i >= createLimit {
				continue
			}
		}

		t.fillUserLocalMeta(chatInfo, dialog)

		res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatResync{
			ChatInfo: chatInfo,
			EventMeta: simplevent.EventMeta{
				Type: bridgev2.RemoteEventChatResync,
				LogContext: func(c zerolog.Context) zerolog.Context {
					return c.Str("update", "sync")
				},
				PortalKey:    portalKey,
				CreatePortal: true,
			},
			CheckNeedsBackfillFunc: func(ctx context.Context, latestMessage *database.Message) (bool, error) {
				if latestMessage == nil {
					return true, nil
				}
				_, latestMessageID, err := ids.ParseMessageID(latestMessage.ID)
				if err != nil {
					panic(err)
				}
				return dialog.TopMessage > latestMessageID, nil
			},
		})
		if err = resultToError(res); err != nil {
			return err
		}

		// Generate a read receipt from the last known read message id
		res = t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.Receipt{
			EventMeta: simplevent.EventMeta{
				Type:      bridgev2.RemoteEventReadReceipt,
				PortalKey: portalKey,
				Sender:    t.mySender(),
			},
			LastTarget:          ids.MakeMessageID(portalKey, dialog.ReadInboxMaxID),
			ReadUpToStreamOrder: int64(dialog.ReadInboxMaxID),
		})
		if err = resultToError(res); err != nil {
			return err
		}
	}
	return nil
}
