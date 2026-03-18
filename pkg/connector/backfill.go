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
func (t *TelegramClient) getTakeoutID(ctx context.Context) (takeoutID int64, err error) {
	if t.metadata.TakeoutInvalidated {
		// TODO should we just backfill without takeout here?
		return 0, fmt.Errorf("takeout invalidated, cannot backfill")
	}
	// Always stop the takeout timeout timer
	if t.stopTakeoutTimer != nil {
		t.stopTakeoutTimer.Stop()
	}
	log := zerolog.Ctx(ctx).With().Str("function", "getTakeoutID").Logger()

	if t.metadata.TakeoutID != 0 {
		// Resume fetching dialogs using takeout and enqueueing them for
		// backfill.
		go t.takeoutDialogsOnce.Do(func() {
			if err = t.syncChats(ctx, takeoutID, false); err != nil {
				log.Err(err).Msg("Failed to takeout dialogs")
			}
		})
		return t.metadata.TakeoutID, nil
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
			if err = t.syncChats(ctx, takeoutID, false); err != nil {
				log.Err(err).Msg("Failed to takeout dialogs")
			}
		})

		t.metadata.TakeoutID = accountTakeout.ID
		return accountTakeout.ID, t.userLogin.Save(ctx)
	}
}

func (t *TelegramClient) stopTakeout(ctx context.Context) error {
	t.takeoutLock.Lock()
	defer t.takeoutLock.Unlock()

	_, err := t.client.API().AccountFinishTakeoutSession(ctx, &tg.AccountFinishTakeoutSessionRequest{Success: true})
	if err != nil {
		return err
	}
	t.metadata.TakeoutID = 0
	return t.userLogin.Save(ctx)
}

func (t *TelegramClient) FetchMessages(ctx context.Context, fetchParams bridgev2.FetchMessagesParams) (*bridgev2.FetchMessagesResponse, error) {
	if t.metadata.IsBot {
		return nil, fmt.Errorf("bots cannot backfill messages")
	}
	log := zerolog.Ctx(ctx).With().Str("method", "FetchMessages").Logger()
	ctx = log.WithContext(ctx)

	var takeoutID int64
	var err error
	if (t.main.Config.Takeout.ForwardBackfill && fetchParams.Forward) || (t.main.Config.Takeout.BackwardBackfill && !fetchParams.Forward) {
		t.takeoutLock.Lock()
		defer t.takeoutLock.Unlock()
		takeoutID, err = t.getTakeoutID(ctx)
		if err != nil {
			return nil, err
		}

		if takeoutID != 0 {
			defer func() {
				if t.stopTakeoutTimer == nil {
					t.stopTakeoutTimer = time.AfterFunc(max(time.Hour, time.Duration(t.main.Bridge.Config.Backfill.Queue.BatchDelay*2)), sync.OnceFunc(func() { t.stopTakeout(ctx) }))
				} else {
					t.stopTakeoutTimer.Reset(max(time.Hour, time.Duration(t.main.Bridge.Config.Backfill.Queue.BatchDelay*2)))
				}
			}()
		}
	}

	peer, topicID, err := t.inputPeerForPortalID(ctx, fetchParams.Portal.ID)
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
	var req bin.Object
	if topicID == ids.TopicIDSpaceRoom {
		return nil, nil
	} else if topicID > 0 {
		req = &tg.MessagesGetRepliesRequest{
			Peer:     peer,
			MsgID:    topicID,
			Limit:    fetchParams.Count,
			MinID:    minID,
			OffsetID: offsetID,
		}
	} else {
		req = &tg.MessagesGetHistoryRequest{
			Peer:     peer,
			Limit:    fetchParams.Count,
			MinID:    minID,
			OffsetID: offsetID,
		}
	}
	if takeoutID != 0 {
		req = &tg.InvokeWithTakeoutRequest{TakeoutID: takeoutID, Query: req}
	}
	log.Info().Any("req", req).Msg("Fetching messages")
	msgs, err := APICallWithUpdates(ctx, t, func() (tg.ModifiedMessagesMessages, error) {
		var box tg.MessagesMessagesBox
		// TODO a single request can only fetch 100 messages, use multiple requests if the requested count is higher
		err = t.client.Invoke(ctx, req, &box)
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
			t.metadata.TakeoutID = 0
			t.metadata.TakeoutInvalidated = true
			err := t.userLogin.Save(ctx)
			if err != nil {
				log.Err(err).Msg("Failed to save user login after clearing takeout ID")
			} else {
				log.Debug().Msg("Cleared invalid takeout ID")
			}
		}
		return nil, err
	}

	messages := msgs.GetMessages()
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

		sender := t.getEventSender(message, !portal.Metadata.(*PortalMetadata).IsSuperGroup)
		intent, ok := portal.GetIntentFor(ctx, sender, t.userLogin, bridgev2.RemoteEventBackfill)
		if !ok {
			continue
		}
		converted, err := t.convertToMatrix(ctx, portal, intent, message)
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
	peerType, _, topicID, err := ids.ParsePortalID(portal.ID)
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
		if topicID == ids.TopicIDSpaceRoom {
			return 0
		} else if topicID > 0 {
			return c.main.Bridge.Config.Backfill.Queue.GetOverride("topic", "supergroup")
		} else if portal.Metadata.(*PortalMetadata).IsSuperGroup {
			return c.main.Bridge.Config.Backfill.Queue.GetOverride("supergroup")
		} else {
			return c.main.Bridge.Config.Backfill.Queue.GetOverride("channel")
		}
	default:
		log.Error().Str("peer_type", string(peerType)).Msg("unknown peer type")
		return 0
	}
}
