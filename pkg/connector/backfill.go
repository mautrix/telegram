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

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/gotd/bin"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tgerr"
)

var (
	_ bridgev2.BackfillingNetworkAPI           = (*TelegramClient)(nil)
	_ bridgev2.BackfillingNetworkAPIWithLimits = (*TelegramClient)(nil)
)

// getTakeoutID blocks until the takeout ID is available.
func (tc *TelegramClient) getTakeoutID(ctx context.Context) (takeoutID int64, err error) {
	// Always stop the takeout timeout timer
	if tc.stopTakeoutTimer != nil {
		tc.stopTakeoutTimer.Stop()
	}
	log := zerolog.Ctx(ctx).With().Str("function", "getTakeoutID").Logger()

	if tc.metadata.TakeoutID != 0 {
		// Resume fetching dialogs using takeout and enqueueing them for
		// backfill.
		go tc.takeoutDialogsOnce.Do(func() {
			if err = tc.syncChats(ctx, takeoutID, false, false); err != nil {
				log.Err(err).Msg("Failed to takeout dialogs")
			}
		})
		return tc.metadata.TakeoutID, nil
	}

	tc.stopTakeoutTimer = time.AfterFunc(max(time.Hour, time.Duration(tc.main.Bridge.Config.Backfill.Queue.BatchDelay*2)), sync.OnceFunc(func() { tc.stopTakeout(ctx) }))

	for {
		tc.takeoutAccepted.Clear()

		accountTakeout, err := tc.client.API().AccountInitTakeoutSession(ctx, &tg.AccountInitTakeoutSessionRequest{
			MessageUsers:      true,
			MessageChats:      true,
			MessageMegagroups: true,
			MessageChannels:   true,
			Files:             true,
			FileMaxSize:       min(tc.main.maxFileSize, 2000*1024*1024),
		})
		if rpcErr, ok := tgerr.As(err); ok && rpcErr.IsOneOf(tg.ErrTakeoutInitDelay) {
			log.Warn().
				Err(err).
				Int("delay", rpcErr.Argument).
				Msg("Takeout requested, will wait for retry request or delay")
			tc.takeoutAccepted.WaitTimeout(time.Duration(rpcErr.Argument) * time.Second)
			continue
		} else if err != nil {
			return 0, err
		}

		// Fetch all dialogs using takeout and enqueue them for backfill.
		go tc.takeoutDialogsOnce.Do(func() {
			if err = tc.syncChats(ctx, takeoutID, false, false); err != nil {
				log.Err(err).Msg("Failed to takeout dialogs")
			}
		})

		tc.metadata.TakeoutID = accountTakeout.ID
		return accountTakeout.ID, tc.userLogin.Save(ctx)
	}
}

func (tc *TelegramClient) stopTakeout(ctx context.Context) error {
	tc.takeoutLock.Lock()
	defer tc.takeoutLock.Unlock()

	_, err := tc.client.API().AccountFinishTakeoutSession(ctx, &tg.AccountFinishTakeoutSessionRequest{Success: true})
	if err != nil {
		return err
	}
	tc.metadata.TakeoutID = 0
	return tc.userLogin.Save(ctx)
}

func (tc *TelegramClient) FetchMessages(ctx context.Context, fetchParams bridgev2.FetchMessagesParams) (*bridgev2.FetchMessagesResponse, error) {
	if tc.metadata.IsBot {
		return nil, fmt.Errorf("bots cannot backfill messages")
	}
	log := zerolog.Ctx(ctx).With().Str("method", "FetchMessages").Logger()
	ctx = log.WithContext(ctx)

	var takeoutID int64
	var err error
	if (tc.main.Config.Takeout.ForwardBackfill && fetchParams.Forward) || (tc.main.Config.Takeout.BackwardBackfill && !fetchParams.Forward) {
		tc.takeoutLock.Lock()
		defer tc.takeoutLock.Unlock()
		takeoutID, err = tc.getTakeoutID(ctx)
		if err != nil {
			return nil, err
		}

		if takeoutID != 0 {
			defer func() {
				if tc.stopTakeoutTimer == nil {
					tc.stopTakeoutTimer = time.AfterFunc(max(time.Hour, time.Duration(tc.main.Bridge.Config.Backfill.Queue.BatchDelay*2)), sync.OnceFunc(func() { tc.stopTakeout(ctx) }))
				} else {
					tc.stopTakeoutTimer.Reset(max(time.Hour, time.Duration(tc.main.Bridge.Config.Backfill.Queue.BatchDelay*2)))
				}
			}()
		}
	}

	peer, topicID, err := tc.inputPeerForPortalID(ctx, fetchParams.Portal.ID)
	if err != nil {
		return nil, err
	}

	var minID, offsetID int
	if fetchParams.AnchorMessage != nil {
		if fetchParams.Forward {
			_, minID, err = ids.ParseMessageID(fetchParams.AnchorMessage.ID)
		} else {
			_, offsetID, err = ids.ParseMessageID(fetchParams.AnchorMessage.ID)
		}
		if err != nil {
			return nil, err
		}
	}
	if fetchParams.Portal.Metadata.(*PortalMetadata).IsForumGeneral {
		topicID = 1
	}
	if topicID == ids.TopicIDSpaceRoom {
		return nil, nil
	}
	limit := fetchParams.Count
	const chunkLimit = 100
	makeReq := func() bin.Object {
		if topicID > 0 {
			return &tg.MessagesGetRepliesRequest{
				Peer:     peer,
				MsgID:    topicID,
				Limit:    min(limit, chunkLimit),
				MinID:    minID,
				OffsetID: offsetID,
			}
		}
		return &tg.MessagesGetHistoryRequest{
			Peer:     peer,
			Limit:    min(limit, chunkLimit),
			MinID:    minID,
			OffsetID: offsetID,
		}
	}
	var messages []tg.MessageClass
	requestCount := 0
	for limit > 0 {
		requestCount++
		req := makeReq()
		if takeoutID != 0 {
			req = &tg.InvokeWithTakeoutRequest{TakeoutID: takeoutID, Query: req}
		}
		log.Info().Any("req", req).Msg("Fetching messages")
		resp, err := APICallWithUpdates(ctx, tc, func() (tg.ModifiedMessagesMessages, error) {
			var box tg.MessagesMessagesBox
			retry := true
			attempts := 0
			var err error
			for retry && attempts < 5 {
				retry, err = tgerr.FloodWait(ctx, tc.client.Invoke(ctx, req, &box))
				attempts++
			}
			if err != nil {
				return nil, err
			}
			msgs, ok := box.Messages.(tg.ModifiedMessagesMessages)
			if !ok {
				return nil, fmt.Errorf("unsupported messages type %T", box.Messages)
			}
			return msgs, nil
		})
		if err != nil {
			if tgerr.Is(err, tg.ErrTakeoutInvalid) {
				tc.metadata.TakeoutID = 0
				err := tc.userLogin.Save(ctx)
				if err != nil {
					log.Err(err).Msg("Failed to save user login after clearing takeout ID")
				} else {
					log.Debug().Msg("Cleared invalid takeout ID")
				}
			}
			return nil, err
		}
		newMessages := resp.GetMessages()
		if messages == nil {
			messages = newMessages
		} else {
			messages = append(messages, resp.GetMessages()...)
		}
		if len(newMessages) < chunkLimit || !fetchParams.Forward {
			break
		}
		limit -= len(newMessages)
		offsetID = newMessages[len(newMessages)-1].GetID()
		if takeoutID == 0 {
			waitTime := time.Duration(min(requestCount*2, 15)) * time.Second
			log.Debug().
				Dur("wait_time", waitTime).
				Msg("Not using takeout, waiting before requesting another batch of messages")
			select {
			case <-time.After(waitTime):
			case <-ctx.Done():
				return nil, ctx.Err()
			}
		}
	}

	portal := fetchParams.Portal

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
			} else if !fetchParams.Forward && msg.GetID() >= stopAt {
				// If we are doing backwards backfill and we get a message more
				// recent than the anchor message, skip it.
				log.Debug().Msg("skipping message past anchor message")
				continue
			}
		}

		message, ok := msg.(*tg.Message)
		if !ok {
			log.Debug().Str("type", msg.TypeName()).Msg("skipping backfilling unsupported message type")
			continue
		}

		sender := tc.getEventSender(message, !portal.Metadata.(*PortalMetadata).IsSuperGroup)
		intent, ok := portal.GetIntentFor(ctx, sender, tc.userLogin, bridgev2.RemoteEventBackfill)
		if !ok {
			continue
		}
		converted, err := tc.convertToMatrix(ctx, portal, intent, message)
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
			reactionsList, _, customEmojis, err := tc.computeReactionsList(ctx, message.PeerID, message.ID, reactions)
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
					Sender:    tc.senderForUserID(peer.UserID),
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

func (tc *TelegramClient) GetBackfillMaxBatchCount(ctx context.Context, portal *bridgev2.Portal, task *database.BackfillTask) int {
	log := zerolog.Ctx(ctx).With().
		Str("method", "GetBackfillMaxBatchCount").
		Logger()
	peerType, _, topicID, err := ids.ParsePortalID(portal.ID)
	if err != nil {
		log.Err(err).Msg("failed to parse portal ID")
		return 0
	}
	switch peerType {
	case ids.PeerTypeUser:
		return tc.main.Bridge.Config.Backfill.Queue.GetOverride("user")
	case ids.PeerTypeChat:
		return tc.main.Bridge.Config.Backfill.Queue.GetOverride("normal_group")
	case ids.PeerTypeChannel:
		if topicID == ids.TopicIDSpaceRoom {
			return 0
		} else if topicID > 0 {
			return tc.main.Bridge.Config.Backfill.Queue.GetOverride("topic", "supergroup")
		} else if portal.Metadata.(*PortalMetadata).IsSuperGroup {
			return tc.main.Bridge.Config.Backfill.Queue.GetOverride("supergroup")
		} else {
			return tc.main.Bridge.Config.Backfill.Queue.GetOverride("channel")
		}
	default:
		log.Error().Str("peer_type", string(peerType)).Msg("unknown peer type")
		return 0
	}
}
