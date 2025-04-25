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

	"github.com/gotd/td/tg"
	"github.com/rs/zerolog"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/bridgev2/simplevent"

	"go.mau.fi/mautrix-telegram/pkg/connector/emojis"
	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
)

func (t *TelegramClient) computeReactionsList(ctx context.Context, peer tg.PeerClass, msgID int, msgReactions tg.MessageReactions) (reactions []tg.MessagePeerReaction, isFull bool, customEmojis map[networkid.EmojiID]emojis.EmojiInfo, err error) {
	log := zerolog.Ctx(ctx).With().Str("fn", "computeReactionsList").Logger()
	var totalCount int
	for _, r := range msgReactions.Results {
		totalCount += r.Count
	}

	reactionsList := msgReactions.RecentReactions
	if totalCount > 0 && len(reactionsList) == 0 && !msgReactions.CanSeeList {
		// We don't know who reacted in a channel, so we can't bridge it properly either
		log.Warn().Msg("Can't see reaction list in channel")
		return
	}

	// TODO
	// if self.peer_type == "channel" and not self.megagroup:
	//     # This should never happen with the previous if
	//     self.log.warning(f"Can see reaction list in channel ({data!s})")
	//     # return

	if len(reactionsList) < totalCount {
		if user, ok := peer.(*tg.PeerUser); ok {
			reactionsList = splitDMReactionCounts(msgReactions.Results, user.UserID, t.telegramUserID)

			// TODO
			// } else if t.isBot {
			// 	// Can't fetch exact reaction senders as a bot
			// 	return

			// TODO should calls to this be limited?
		} else if peer, err := t.inputPeerForPortalID(ctx, t.makePortalKeyFromPeer(peer).ID); err != nil {
			return nil, false, nil, fmt.Errorf("failed to get input peer: %w", err)
		} else {
			reactions, err := APICallWithUpdates(ctx, t, func() (*tg.MessagesMessageReactionsList, error) {
				return t.client.API().MessagesGetMessageReactionsList(ctx, &tg.MessagesGetMessageReactionsListRequest{
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

	customEmojis, err = t.transferEmojisToMatrix(ctx, customEmojiIDs)
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

func (t *TelegramClient) handleTelegramReactions(ctx context.Context, msg *tg.Message) {
	log := zerolog.Ctx(ctx).With().
		Str("handler", "handle_telegram_reactions").
		Int("message_id", msg.ID).
		Logger()

	reactionsList, isFull, customEmojis, err := t.computeReactionsList(ctx, msg.PeerID, msg.ID, msg.Reactions)
	if err != nil {
		log.Err(err).Msg("failed to compute reactions list")
		return
	}

	users := map[networkid.UserID]*bridgev2.ReactionSyncUser{}
	for _, reaction := range reactionsList {
		peer, ok := reaction.PeerID.(*tg.PeerUser)
		if !ok {
			log.Error().Type("peer_id", reaction.PeerID).Msg("unknown peer type")
			return
		}
		userID := ids.MakeUserID(peer.UserID)
		reactionLimit, err := t.getReactionLimit(ctx, userID)
		if err != nil {
			reactionLimit = 1
			log.Err(err).Int64("id", peer.UserID).Msg("failed to get reaction limit")
		}
		if _, ok := users[userID]; !ok {
			users[userID] = &bridgev2.ReactionSyncUser{HasAllReactions: isFull, MaxCount: reactionLimit}
		}

		emojiID, emoji, err := computeEmojiAndID(reaction.Reaction, customEmojis)
		if err != nil {
			log.Err(err).Msg("failed to compute emoji and ID")
			return
		}

		users[userID].Reactions = append(users[userID].Reactions, &bridgev2.BackfillReaction{
			Timestamp: time.Unix(int64(reaction.Date), 0),
			Sender:    t.senderForUserID(peer.UserID),
			EmojiID:   emojiID,
			Emoji:     emoji,
		})
	}

	t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ReactionSync{
		EventMeta: simplevent.EventMeta{
			Type: bridgev2.RemoteEventReactionSync,
			LogContext: func(c zerolog.Context) zerolog.Context {
				return c.Int("message_id", msg.ID)
			},
			PortalKey: t.makePortalKeyFromPeer(msg.PeerID),
		},
		TargetMessage: ids.GetMessageIDFromMessage(msg),
		Reactions:     &bridgev2.ReactionSyncData{Users: users, HasAllUsers: isFull},
	})
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

func (t *TelegramClient) getReactionLimit(ctx context.Context, sender networkid.UserID) (limit int, err error) {
	config, err := t.getAppConfigCached(ctx)
	if err != nil {
		return 0, err
	}

	ghost, err := t.main.Bridge.GetGhostByID(ctx, sender)
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

func (t *TelegramClient) pollForReactions(ctx context.Context, portalKey networkid.PortalKey, inputPeer tg.InputPeerClass) error {
	log := zerolog.Ctx(ctx).With().
		Stringer("portal_key", portalKey).
		Str("action", "poll_for_reactions").
		Logger()

	log.Debug().Msg("Polling reactions for recent messages")

	messages, err := t.main.Bridge.DB.Message.GetLastNInPortal(ctx, portalKey, 20)
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

	updates, err := APICallWithUpdates(ctx, t, func() (*tg.Updates, error) {
		u, err := t.client.API().MessagesGetMessagesReactions(ctx, &tg.MessagesGetMessagesReactionsRequest{
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
		if reaction, ok := update.(*tg.UpdateMessageReactions); ok {
			dbMsg, err := t.main.Bridge.DB.Message.GetFirstPartByID(ctx, t.loginID, ids.MakeMessageID(portalKey, reaction.MsgID))
			if err != nil {
				return fmt.Errorf("failed to get message from database: %w", err)
			} else if dbMsg == nil {
				return fmt.Errorf("message not found in database: %w", err)
			}

			reactionsList, isFull, customEmojis, err := t.computeReactionsList(ctx, reaction.Peer, reaction.MsgID, reaction.Reactions)
			if err != nil {
				return fmt.Errorf("failed to compute reactions list: %w", err)
			}

			users := map[networkid.UserID]*bridgev2.ReactionSyncUser{}
			for _, reaction := range reactionsList {
				peer, ok := reaction.PeerID.(*tg.PeerUser)
				if !ok {
					return fmt.Errorf("unknown peer type %T", reaction.PeerID)
				}
				userID := ids.MakeUserID(peer.UserID)
				reactionLimit, err := t.getReactionLimit(ctx, userID)
				if err != nil {
					reactionLimit = 1
					log.Err(err).Int64("id", peer.UserID).Msg("failed to get reaction limit")
				}
				if _, ok := users[userID]; !ok {
					users[userID] = &bridgev2.ReactionSyncUser{HasAllReactions: isFull, MaxCount: reactionLimit}
				}

				emojiID, emoji, err := computeEmojiAndID(reaction.Reaction, customEmojis)
				if err != nil {
					return fmt.Errorf("failed to compute emoji and ID: %w", err)
				}

				users[userID].Reactions = append(users[userID].Reactions, &bridgev2.BackfillReaction{
					Timestamp: time.Unix(int64(reaction.Date), 0),
					Sender:    t.senderForUserID(peer.UserID),
					EmojiID:   emojiID,
					Emoji:     emoji,
				})
			}
			t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ReactionSync{
				EventMeta: simplevent.EventMeta{
					Type: bridgev2.RemoteEventReactionSync,
					LogContext: func(c zerolog.Context) zerolog.Context {
						return c.Int("message_id", reaction.MsgID)
					},
					PortalKey: dbMsg.Room,
				},
				TargetMessage: dbMsg.ID,
				Reactions:     &bridgev2.ReactionSyncData{Users: users, HasAllUsers: isFull},
			})
		} else {
			log.Warn().Type("update_type", update).Msg("Unexpected update type in get reactions response")
		}
	}
	return nil
}
