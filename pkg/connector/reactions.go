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
	"time"

	"github.com/rs/zerolog"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/bridgev2/simplevent"

	"go.mau.fi/mautrix-telegram/pkg/connector/emojis"
	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

func (tc *TelegramClient) computeReactionsList(ctx context.Context, peer tg.PeerClass, msgID int, msgReactions tg.MessageReactions) (reactions []tg.MessagePeerReaction, isFull bool, customEmojis map[networkid.EmojiID]emojis.EmojiInfo, err error) {
	log := zerolog.Ctx(ctx).With().Str("fn", "computeReactionsList").Logger()
	var totalCount int
	for _, r := range msgReactions.Results {
		totalCount += r.Count
	}

	reactionsList := msgReactions.RecentReactions
	if totalCount > 0 && len(reactionsList) == 0 && !msgReactions.CanSeeList {
		// We don't know who reacted in a channel, so we can't bridge it properly either
		log.Trace().Msg("Can't see reaction list in channel")
		return
	}

	// TODO
	// if self.peer_type == "channel" and not self.megagroup:
	//     # This should never happen with the previous if
	//     self.log.warning(f"Can see reaction list in channel ({data!s})")
	//     # return

	if len(reactionsList) < totalCount {
		if user, ok := peer.(*tg.PeerUser); ok {
			reactionsList = splitDMReactionCounts(msgReactions.Results, user.UserID, tc.telegramUserID)
		} else if tc.metadata.IsBot {
			// Can't fetch exact reaction senders as a bot
			return

			// TODO remove redundant peer roundtrip, just add a peer -> input peer helper
		} else if peer, _, err := tc.inputPeerForPortalID(ctx, tc.makePortalKeyFromPeer(peer, 0).ID); err != nil {
			return nil, false, nil, fmt.Errorf("failed to get input peer: %w", err)
		} else {
			// TODO should calls to this be limited?
			reactions, err := APICallWithUpdates(ctx, tc, func() (*tg.MessagesMessageReactionsList, error) {
				return tc.client.API().MessagesGetMessageReactionsList(ctx, &tg.MessagesGetMessageReactionsListRequest{
					Peer: peer, ID: msgID, Limit: 100,
				})
			})
			if err != nil {
				return nil, false, nil, fmt.Errorf("failed to get reactions list: %w", err)
			}
			reactionsList = reactions.Reactions
		}
	}

	var customEmojiIDs []int64
	for _, reaction := range reactionsList {
		if e, ok := reaction.Reaction.(*tg.ReactionCustomEmoji); ok {
			customEmojiIDs = append(customEmojiIDs, e.DocumentID)
		} else if reaction.Reaction.TypeID() != tg.ReactionEmojiTypeID {
			return nil, false, nil, fmt.Errorf("unsupported reaction type %T", reaction.Reaction)
		}
	}

	customEmojis, err = tc.transferEmojisToMatrix(ctx, customEmojiIDs)
	return reactionsList, len(reactionsList) == totalCount, customEmojis, err
}

func computeEmojiAndID(reaction tg.ReactionClass, customEmojis map[networkid.EmojiID]emojis.EmojiInfo) (emojiID networkid.EmojiID, emoji string, err error) {
	if r, ok := reaction.(*tg.ReactionCustomEmoji); ok {
		emojiID = ids.MakeEmojiIDFromDocumentID(r.DocumentID)
		emoji = customEmojis[emojiID].Emoji
		if emoji == "" {
			emoji = string(customEmojis[emojiID].EmojiURI)
		}
	} else if r, ok := reaction.(*tg.ReactionEmoji); ok {
		emojiID = ids.MakeEmojiIDFromEmoticon(r.Emoticon)
		emoji = r.Emoticon
	} else {
		return "", "", fmt.Errorf("invalid reaction type %T", reaction)
	}
	return
}

func (tc *TelegramClient) prepareReactionSync(ctx context.Context, peer tg.PeerClass, msgID int, reactions tg.MessageReactions) (*bridgev2.ReactionSyncData, error) {
	reactionsList, isFull, customEmojis, err := tc.computeReactionsList(ctx, peer, msgID, reactions)
	if err != nil {
		return nil, fmt.Errorf("failed to compute reactions: %w", err)
	}

	log := zerolog.Ctx(ctx)
	users := map[networkid.UserID]*bridgev2.ReactionSyncUser{}
	for _, reaction := range reactionsList {
		var userID networkid.UserID
		var eventSender bridgev2.EventSender
		switch senderPeer := reaction.PeerID.(type) {
		case *tg.PeerUser:
			userID = ids.MakeUserID(senderPeer.UserID)
			eventSender = tc.senderForUserID(senderPeer.UserID)
		case *tg.PeerChannel:
			userID = ids.MakeChannelUserID(senderPeer.ChannelID)
			eventSender = bridgev2.EventSender{
				Sender:   userID,
				IsFromMe: reaction.My && tc.main.Bridge.Config.SplitPortals,
			}
		default:
			log.Debug().Type("peer_type", reaction.PeerID).Msg("Ignoring reaction from non-user peer")
			continue
		}
		reactionLimit, err := tc.getReactionLimit(ctx, userID)
		if err != nil {
			reactionLimit = 1
			log.Err(err).Str("id", string(userID)).Msg("failed to get reaction limit")
		}
		if _, ok := users[userID]; !ok {
			users[userID] = &bridgev2.ReactionSyncUser{HasAllReactions: isFull, MaxCount: reactionLimit}
		}

		emojiID, emoji, err := computeEmojiAndID(reaction.Reaction, customEmojis)
		if err != nil {
			log.Err(err).Msg("Failed to compute emoji and ID for reaction")
			continue
		}

		users[userID].Reactions = append(users[userID].Reactions, &bridgev2.BackfillReaction{
			Timestamp: time.Unix(int64(reaction.Date), 0),
			Sender:    eventSender,
			EmojiID:   emojiID,
			Emoji:     emoji,
		})
	}
	return &bridgev2.ReactionSyncData{Users: users, HasAllUsers: isFull}, nil
}

func (tc *TelegramClient) handleTelegramReactions(ctx context.Context, peer tg.PeerClass, topicID, msgID int, reactions tg.MessageReactions) error {
	ctx = zerolog.Ctx(ctx).With().
		Str("handler", "handle_telegram_reactions").
		Int("message_id", msgID).
		Logger().WithContext(ctx)

	data, err := tc.prepareReactionSync(ctx, peer, msgID, reactions)
	if err != nil {
		return err
	}

	return resultToError(tc.main.Bridge.QueueRemoteEvent(tc.userLogin, &simplevent.ReactionSync{
		EventMeta: simplevent.EventMeta{
			Type: bridgev2.RemoteEventReactionSync,
			LogContext: func(c zerolog.Context) zerolog.Context {
				return c.Int("message_id", msgID)
			},
			PortalKey: tc.makePortalKeyFromPeer(peer, topicID),
		},
		TargetMessage: ids.MakeMessageID(peer, msgID),
		Reactions:     data,
	}))
}

func splitDMReactionCounts(res []tg.ReactionCount, theirUserID, myUserID int64) (reactions []tg.MessagePeerReaction) {
	for _, item := range res {
		if item.Count == 2 || item.ChosenOrder > 0 {
			reactions = append(reactions, tg.MessagePeerReaction{
				Reaction: item.Reaction,
				PeerID:   &tg.PeerUser{UserID: myUserID},
			})
		}

		if item.Count == 2 {
			reactions = append(reactions, tg.MessagePeerReaction{
				Reaction: item.Reaction,
				PeerID:   &tg.PeerUser{UserID: theirUserID},
			})
		}
	}
	return
}

func (tc *TelegramClient) getReactionLimit(ctx context.Context, sender networkid.UserID) (limit int, err error) {
	config, err := tc.getAppConfigCached(ctx)
	if err != nil {
		return 0, err
	}

	ghost, err := tc.main.Bridge.GetGhostByID(ctx, sender)
	if err != nil {
		return 0, err
	}
	if ghost.Metadata.(*GhostMetadata).IsPremium {
		if maxReactions, ok := config["reactions_user_max_premium"].(float64); ok {
			return int(maxReactions), nil
		} else {
			return 3, nil
		}
	} else {
		if maxReactions, ok := config["reactions_user_max_default"].(float64); ok {
			return int(maxReactions), nil
		} else {
			return 1, nil
		}
	}
}

func (tc *TelegramClient) maybePollForReactions(ctx context.Context, portal *bridgev2.Portal) error {
	// Only poll for reactions in supergroups
	if tc.metadata.IsBot || portal == nil || !portal.Metadata.(*PortalMetadata).IsSuperGroup || portal.RoomType == database.RoomTypeSpace {
		return nil
	}

	tc.prevReactionPollLock.Lock()
	prev, ok := tc.prevReactionPoll[portal.PortalKey]
	if ok && time.Since(prev) > 20*time.Second {
		ok = false
		tc.prevReactionPoll[portal.PortalKey] = time.Now()
	}
	tc.prevReactionPollLock.Unlock()
	if ok {
		return nil
	}
	return tc.pollForReactions(ctx, portal.PortalKey)
}

func (tc *TelegramClient) pollForReactions(ctx context.Context, portalKey networkid.PortalKey) error {
	inputPeer, _, parseErr := tc.inputPeerForPortalID(ctx, portalKey.ID)
	if parseErr != nil {
		return parseErr
	}
	log := zerolog.Ctx(ctx).With().
		Stringer("portal_key", portalKey).
		Str("action", "poll_for_reactions").
		Logger()

	log.Debug().Msg("Polling reactions for recent messages")

	messages, err := tc.main.Bridge.DB.Message.GetLastNInPortal(ctx, portalKey, 20)
	if err != nil {
		return err
	}

	messageIDs := make([]int, len(messages))
	for i, msg := range messages {
		_, messageIDs[i], err = ids.ParseMessageID(msg.ID)
		if err != nil {
			return err
		}
	}

	updates, err := APICallWithUpdates(ctx, tc, func() (*tg.Updates, error) {
		u, err := tc.client.API().MessagesGetMessagesReactions(ctx, &tg.MessagesGetMessagesReactionsRequest{
			Peer: inputPeer,
			ID:   messageIDs,
		})
		if err != nil {
			return nil, err
		}
		if updates, ok := u.(*tg.Updates); ok {
			return updates, nil
		} else {
			return nil, fmt.Errorf("unexpected updates type %T", u)
		}
	})
	if err != nil {
		return fmt.Errorf("failed to get messages reactions: %w", err)
	}

	for _, update := range updates.Updates {
		reaction, ok := update.(*tg.UpdateMessageReactions)
		if !ok {
			log.Warn().Type("update_type", update).Msg("Unexpected update type in get reactions response")
			continue
		}
		dbMsg, err := tc.main.Bridge.DB.Message.GetFirstPartByID(ctx, tc.loginID, ids.MakeMessageID(portalKey, reaction.MsgID))
		if err != nil {
			return fmt.Errorf("failed to get message from database: %w", err)
		} else if dbMsg == nil {
			return fmt.Errorf("message not found in database: %w", err)
		}

		data, err := tc.prepareReactionSync(ctx, reaction.Peer, reaction.MsgID, reaction.Reactions)
		if err != nil {
			return err
		}

		res := tc.main.Bridge.QueueRemoteEvent(tc.userLogin, &simplevent.ReactionSync{
			EventMeta: simplevent.EventMeta{
				Type: bridgev2.RemoteEventReactionSync,
				LogContext: func(c zerolog.Context) zerolog.Context {
					return c.Int("message_id", reaction.MsgID)
				},
				PortalKey: dbMsg.Room,
			},
			TargetMessage: dbMsg.ID,
			Reactions:     data,
		})
		if err = resultToError(res); err != nil {
			return err
		}
	}
	return nil
}
