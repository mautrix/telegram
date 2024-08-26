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

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
)

func (t *TelegramClient) computeReactionsList(ctx context.Context, msg *tg.Message) (reactions []tg.MessagePeerReaction, isFull bool, customEmojis map[networkid.EmojiID]string, err error) {
	log := zerolog.Ctx(ctx).With().Str("fn", "computeReactionsList").Logger()
	if _, set := msg.GetReactions(); !set {
		return
	}

	var totalCount int
	for _, r := range msg.Reactions.Results {
		totalCount += r.Count
	}

	reactionsList := msg.Reactions.RecentReactions
	if totalCount > 0 && len(reactionsList) == 0 && !msg.Reactions.CanSeeList {
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
		if user, ok := msg.PeerID.(*tg.PeerUser); ok {
			reactionsList = splitDMReactionCounts(msg.Reactions.Results, user.UserID, t.telegramUserID)

			// TODO
			// } else if t.isBot {
			// 	// Can't fetch exact reaction senders as a bot
			// 	return

			// TODO should calls to this be limited?
		} else if peer, err := t.inputPeerForPortalID(ctx, ids.MakePortalKey(msg.PeerID, t.loginID).ID); err != nil {
			return nil, false, nil, fmt.Errorf("failed to get input peer: %w", err)
		} else {
			reactions, err := APICallWithUpdates(ctx, t, func() (*tg.MessagesMessageReactionsList, error) {
				return t.client.API().MessagesGetMessageReactionsList(ctx, &tg.MessagesGetMessageReactionsListRequest{
					Peer: peer, ID: msg.ID, Limit: 100,
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

func computeEmojiAndID(reaction tg.ReactionClass, customEmojis map[networkid.EmojiID]string) (emojiID networkid.EmojiID, emoji string, err error) {
	if r, ok := reaction.(*tg.ReactionCustomEmoji); ok {
		emojiID = ids.MakeEmojiIDFromDocumentID(r.DocumentID)
		emoji = customEmojis[emojiID]
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

	dbMsg, err := t.main.Bridge.DB.Message.GetFirstPartByID(ctx, t.loginID, ids.GetMessageIDFromMessage(msg))
	if err != nil {
		log.Err(err).Msg("failed to get message from database")
		return
	} else if dbMsg == nil {
		log.Warn().Msg("message not found in database")
		return
	}

	reactionsList, isFull, customEmojis, err := t.computeReactionsList(ctx, msg)
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
			Sender: bridgev2.EventSender{
				IsFromMe:    reaction.My,
				SenderLogin: ids.MakeUserLoginID(peer.UserID),
				Sender:      userID,
			},
			EmojiID: emojiID,
			Emoji:   emoji,
		})
	}

	t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ReactionSync{
		EventMeta: simplevent.EventMeta{
			Type: bridgev2.RemoteEventReactionSync,
			LogContext: func(c zerolog.Context) zerolog.Context {
				return c.Int("message_id", msg.ID)
			},
			PortalKey: dbMsg.Room,
		},
		TargetMessage: dbMsg.ID,
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
		return int(config["reactions_user_max_premium"].(float64)), nil
	} else {
		return int(config["reactions_user_max_default"].(float64)), nil
	}
}
