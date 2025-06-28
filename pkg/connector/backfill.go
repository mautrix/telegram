// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2025 Sumner Evans
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
	"slices"
	"sync"
	"time"

	"github.com/rs/zerolog"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/networkid"

	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tgerr"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
)

// getTakeoutID blocks until the takeout ID is available.
func (t *TelegramClient) getTakeoutID(ctx context.Context) (takeoutID int64, err error) {
	// Always stop the takeout timeout timer
	if t.stopTakeoutTimer != nil {
		t.stopTakeoutTimer.Stop()
	}
	log := zerolog.Ctx(ctx).With().Str("function", "getTakeoutID").Logger()

	if t.userLogin.Metadata.(*UserLoginMetadata).TakeoutID != 0 {
		// Resume fetching dialogs using takeout and enqueueing them for
		// backfill.
		go t.takeoutDialogsOnce.Do(func() {
			if err = t.takeoutDialogs(ctx, takeoutID); err != nil {
				log.Err(err).Msg("Failed to takeout dialogs")
			}
		})
		return t.userLogin.Metadata.(*UserLoginMetadata).TakeoutID, nil
	}

	t.stopTakeoutTimer = time.AfterFunc(max(time.Hour, time.Duration(t.main.Bridge.Config.Backfill.Queue.BatchDelay*2)), sync.OnceFunc(func() { t.stopTakeout(ctx) }))

	for {
		t.takeoutAccepted.Clear()

		accountTakeout, err := t.client.API().AccountInitTakeoutSession(ctx, &tg.AccountInitTakeoutSessionRequest{
			MessageUsers:      true,
			MessageChats:      true,
			MessageMegagroups: true,
			MessageChannels:   true,
			Files:             true,
			FileMaxSize:       min(t.main.maxFileSize, 2000*1024*1024),
		})
		if rpcErr, ok := tgerr.As(err); ok && rpcErr.IsOneOf(tg.ErrTakeoutInitDelay) {
			log.Warn().
				Err(err).
				Int("delay", rpcErr.Argument).
				Msg("Takeout requested, will wait for retry request or delay")
			t.takeoutAccepted.WaitTimeout(time.Duration(rpcErr.Argument) * time.Second)
			continue
		} else if err != nil {
			return 0, err
		}

		// Fetch all dialogs using takeout and enqueue them for backfill.
		go t.takeoutDialogsOnce.Do(func() {
			if err = t.takeoutDialogs(ctx, takeoutID); err != nil {
				log.Err(err).Msg("Failed to takeout dialogs")
			}
		})

		t.userLogin.Metadata.(*UserLoginMetadata).TakeoutID = accountTakeout.ID
		return accountTakeout.ID, t.userLogin.Save(ctx)
	}
}

func (t *TelegramClient) takeoutDialogs(ctx context.Context, takeoutID int64) error {
	log := zerolog.Ctx(ctx).With().Str("loop", "chat_fetch").Logger()
	if t.userLogin.Metadata.(*UserLoginMetadata).TakeoutDialogCrawlDone {
		log.Debug().Msg("Dialogs already crawled")
		return nil
	}

	req := tg.MessagesGetDialogsRequest{
		Limit:      100,
		OffsetPeer: &tg.InputPeerEmpty{},
	}
	if t.userLogin.Metadata.(*UserLoginMetadata).TakeoutDialogCrawlCursor != "" {
		var err error
		req.OffsetPeer, err = t.inputPeerForPortalID(ctx, t.userLogin.Metadata.(*UserLoginMetadata).TakeoutDialogCrawlCursor)
		if err != nil {
			return fmt.Errorf("failed to get input peer for pagination: %w", err)
		}
	}
	for {
		log.Info().Stringer("cursor", req.OffsetPeer).Msg("Fetching dialogs")
		dialogs, err := APICallWithUpdates(ctx, t, func() (tg.ModifiedMessagesDialogs, error) {
			var dialogs tg.MessagesDialogsBox
			err := t.client.Invoke(ctx,
				&tg.InvokeWithTakeoutRequest{TakeoutID: takeoutID, Query: &req},
				&dialogs)
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
			t.userLogin.Metadata.(*UserLoginMetadata).TakeoutDialogCrawlDone = true
			if err = t.userLogin.Save(ctx); err != nil {
				return fmt.Errorf("failed to save user login: %w", err)
			}
			log.Debug().Msg("No more dialogs found")
			return nil
		}

		if req.OffsetPeer.TypeID() == tg.InputPeerEmptyTypeID {
			// This is the first fetch of dialogs, reset the pinned dialogs
			// based on the list.
			if err := t.resetPinnedDialogs(ctx, dialogs.GetDialogs()); err != nil {
				return err
			}
		}

		err = t.handleDialogs(ctx, dialogs, -1)
		if err != nil {
			return fmt.Errorf("failed to handle dialogs: %w", err)
		}

		portalKey := t.makePortalKeyFromPeer(dialogs.GetDialogs()[len(dialogs.GetDialogs())-1].GetPeer())

		if t.userLogin.Metadata.(*UserLoginMetadata).TakeoutDialogCrawlCursor == portalKey.ID {
			t.userLogin.Metadata.(*UserLoginMetadata).TakeoutDialogCrawlDone = true
			t.userLogin.Metadata.(*UserLoginMetadata).TakeoutDialogCrawlCursor = ""
			log.Debug().Msg("No more dialogs found")
			return nil
		} else {
			t.userLogin.Metadata.(*UserLoginMetadata).TakeoutDialogCrawlCursor = portalKey.ID
		}
		if err = t.userLogin.Save(ctx); err != nil {
			return fmt.Errorf("failed to save user login: %w", err)
		}

		req.OffsetPeer, err = t.inputPeerForPortalID(ctx, portalKey.ID)
		if err != nil {
			return fmt.Errorf("failed to get input peer for pagination: %w", err)
		}
	}
}

func (t *TelegramClient) stopTakeout(ctx context.Context) error {
	t.takeoutLock.Lock()
	defer t.takeoutLock.Unlock()

	_, err := t.client.API().AccountFinishTakeoutSession(ctx, &tg.AccountFinishTakeoutSessionRequest{Success: true})
	if err != nil {
		return err
	}
	t.userLogin.Metadata.(*UserLoginMetadata).TakeoutID = 0
	return t.userLogin.Save(ctx)
}

func (t *TelegramClient) FetchMessages(ctx context.Context, fetchParams bridgev2.FetchMessagesParams) (*bridgev2.FetchMessagesResponse, error) {
	log := zerolog.Ctx(ctx).With().Str("method", "FetchMessages").Logger()
	ctx = log.WithContext(ctx)

	var takeoutID int64
	var err error
	if !fetchParams.Forward { // Backwards
		t.takeoutLock.Lock()
		defer t.takeoutLock.Unlock()
		takeoutID, err = t.getTakeoutID(ctx)
		if err != nil {
			return nil, err
		}

		defer func() {
			if t.stopTakeoutTimer == nil {
				t.stopTakeoutTimer = time.AfterFunc(max(time.Hour, time.Duration(t.main.Bridge.Config.Backfill.Queue.BatchDelay*2)), sync.OnceFunc(func() { t.stopTakeout(ctx) }))
			} else {
				t.stopTakeoutTimer.Reset(max(time.Hour, time.Duration(t.main.Bridge.Config.Backfill.Queue.BatchDelay*2)))
			}
		}()
	}

	peer, err := t.inputPeerForPortalID(ctx, fetchParams.Portal.ID)
	if err != nil {
		return nil, err
	}

	req := tg.MessagesGetHistoryRequest{
		Peer:  peer,
		Limit: fetchParams.Count,
	}
	if fetchParams.AnchorMessage != nil {
		if fetchParams.Forward {
			_, req.MinID, err = ids.ParseMessageID(fetchParams.AnchorMessage.ID)
		} else {
			_, req.OffsetID, err = ids.ParseMessageID(fetchParams.AnchorMessage.ID)
		}
		if err != nil {
			return nil, err
		}
	}
	log.Info().Any("req", req).Msg("Fetching messages")
	msgs, err := APICallWithUpdates(ctx, t, func() (tg.ModifiedMessagesMessages, error) {
		var rawMsgs tg.MessagesMessagesClass
		if fetchParams.Forward {
			rawMsgs, err = t.client.API().MessagesGetHistory(ctx, &req)
		} else {
			var messages tg.MessagesMessagesBox
			err = t.client.Invoke(ctx,
				&tg.InvokeWithTakeoutRequest{TakeoutID: takeoutID, Query: &req},
				&messages)
			rawMsgs = messages.Messages
		}
		if err != nil {
			return nil, err
		}
		msgs, ok := rawMsgs.(tg.ModifiedMessagesMessages)
		if !ok {
			return nil, fmt.Errorf("unsupported messages type %T", rawMsgs)
		}
		return msgs, nil
	})
	if err != nil {
		return nil, err
	}

	messages := msgs.GetMessages()

	portal, err := t.main.Bridge.GetPortalByKey(ctx, fetchParams.Portal.PortalKey)
	if err != nil {
		return nil, err
	}

	// If the first message is the last read message, mark the chat as read
	// during backfill.
	markRead := fetchParams.Forward &&
		len(messages) > 0 &&
		portal.Metadata.(*PortalMetadata).ReadUpTo == messages[0].GetID()

	var cursor networkid.PaginationCursor
	if len(messages) > 0 {
		cursor = ids.MakePaginationCursorID(messages[len(messages)-1].GetID())
	}

	var stopAt int
	if fetchParams.AnchorMessage != nil {
		_, stopAt, err = ids.ParseMessageID(fetchParams.AnchorMessage.ID)
		if err != nil {
			return nil, err
		}
		log = log.With().Int("stop_at", stopAt).Logger()
	}

	var backfillMessages []*bridgev2.BackfillMessage
	for _, msg := range messages {
		log := log.With().Int("message_id", msg.GetID()).Logger()
		if stopAt > 0 {
			if fetchParams.Forward && msg.GetID() <= stopAt {
				// If we are doing forward backfill and we get to the anchor
				// message, don't convert any more messages.
				log.Debug().Msg("stopping at anchor message")
				break
			} else if msg.GetID() >= stopAt {
				// If we are doing backwards backfill and we get a message more
				// recent than the anchor message, skip it.
				log.Debug().Msg("skipping message past anchor message")
				continue
			}
		}

		message, ok := msg.(*tg.Message)
		if !ok {
			log.Warn().Str("type", msg.TypeName()).Msg("skipping backfilling unsupported message type")
			continue
		}

		sender := t.getEventSender(message, !portal.Metadata.(*PortalMetadata).IsSuperGroup)
		intent, ok := portal.GetIntentFor(ctx, sender, t.userLogin, bridgev2.RemoteEventBackfill)
		if !ok {
			continue
		}
		converted, err := t.convertToMatrixWithRefetch(ctx, portal, intent, message)
		if err != nil {
			return nil, err
		}

		backfillMessage := bridgev2.BackfillMessage{
			ConvertedMessage: converted,
			Sender:           sender,
			ID:               ids.GetMessageIDFromMessage(message),
			Timestamp:        time.Unix(int64(message.Date), 0),
			StreamOrder:      int64(message.GetID()),
		}

		if reactions, ok := message.GetReactions(); ok {
			reactionsList, _, customEmojis, err := t.computeReactionsList(ctx, message.PeerID, message.ID, reactions)
			if err != nil {
				return nil, err
			}

			for _, reaction := range reactionsList {
				peer, ok := reaction.PeerID.(*tg.PeerUser)
				if !ok {
					return nil, fmt.Errorf("unknown peer type %T", reaction.PeerID)
				}

				emojiID, emoji, err := computeEmojiAndID(reaction.Reaction, customEmojis)
				if err != nil {
					return nil, fmt.Errorf("failed to compute emoji and ID: %w", err)
				}

				backfillMessage.Reactions = append(backfillMessage.Reactions, &bridgev2.BackfillReaction{
					Timestamp: time.Unix(int64(reaction.Date), 0),
					Sender:    t.senderForUserID(peer.UserID),
					EmojiID:   emojiID,
					Emoji:     emoji,
				})
			}
		}

		backfillMessages = append(backfillMessages, &backfillMessage)
	}

	// They are returned with most recent message first, so reverse the order.
	slices.Reverse(backfillMessages)

	return &bridgev2.FetchMessagesResponse{
		Messages: backfillMessages,
		Cursor:   cursor,
		HasMore:  len(backfillMessages) > 0,
		Forward:  fetchParams.Forward,
		MarkRead: markRead,
	}, nil
}

func (c *TelegramClient) GetBackfillMaxBatchCount(ctx context.Context, portal *bridgev2.Portal, task *database.BackfillTask) int {
	log := zerolog.Ctx(ctx).With().
		Str("method", "GetBackfillMaxBatchCount").
		Logger()
	peerType, _, err := ids.ParsePortalID(portal.ID)
	if err != nil {
		log.Err(err).Msg("failed to parse portal ID")
		return 0
	}
	switch peerType {
	case ids.PeerTypeUser:
		return c.main.Bridge.Config.Backfill.Queue.GetOverride("user")
	case ids.PeerTypeChat:
		return c.main.Bridge.Config.Backfill.Queue.GetOverride("normal_group")
	case ids.PeerTypeChannel:
		if portal.Metadata.(*PortalMetadata).IsSuperGroup {
			return c.main.Bridge.Config.Backfill.Queue.GetOverride("supergroup")
		} else {
			return c.main.Bridge.Config.Backfill.Queue.GetOverride("channel")
		}
	default:
		log.Error().Str("peer_type", string(peerType)).Msg("unknown peer type")
		return 0
	}
}
