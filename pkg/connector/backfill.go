package connector

import (
	"context"
	"fmt"
	"slices"
	"time"

	"github.com/gotd/td/tg"
	"github.com/rs/zerolog"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/networkid"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
)

func (t *TelegramClient) FetchMessages(ctx context.Context, fetchParams bridgev2.FetchMessagesParams) (*bridgev2.FetchMessagesResponse, error) {
	log := zerolog.Ctx(ctx).With().
		Str("method", "FetchMessages").
		Logger()
	ctx = log.WithContext(ctx)

	peer, err := t.inputPeerForPortalID(ctx, fetchParams.Portal.ID)
	if err != nil {
		return nil, err
	}

	req := tg.MessagesGetHistoryRequest{
		Peer:  peer,
		Limit: fetchParams.Count,
	}
	if fetchParams.AnchorMessage != nil && !fetchParams.Forward {
		_, req.MaxID, err = ids.ParseMessageID(fetchParams.AnchorMessage.ID)
		if err != nil {
			return nil, err
		}
	}
	msgs, err := APICallWithUpdates(ctx, t, func() (tg.ModifiedMessagesMessages, error) {
		rawMsgs, err := t.client.API().MessagesGetHistory(ctx, &req)
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

	var markRead bool // TODO implement
	messages := msgs.GetMessages()

	var cursor networkid.PaginationCursor
	if len(messages) > 0 {
		cursor = ids.MakePaginationCursorID(messages[len(messages)-1].GetID())
	}

	var stopAt int
	if fetchParams.AnchorMessage != nil && fetchParams.Forward {
		_, stopAt, err = ids.ParseMessageID(fetchParams.AnchorMessage.ID)
		if err != nil {
			return nil, err
		}
	}

	var backfillMessages []*bridgev2.BackfillMessage
	for _, msg := range messages {
		// If we are doing forward backfill and we get to the anchor message,
		// don't convert any more messages.
		if stopAt > 0 && msg.GetID() <= stopAt {
			break
		}

		if msg.TypeID() != tg.MessageTypeID {
			log.Warn().Str("type", msg.TypeName()).Msg("skipping backfilling unsupported message type")
			continue
		}
		message := msg.(*tg.Message)

		portal, err := t.main.Bridge.GetPortalByKey(ctx, fetchParams.Portal.PortalKey)
		if err != nil {
			return nil, err
		}

		sender := t.getEventSender(message)
		intent := portal.GetIntentFor(ctx, sender, t.userLogin, bridgev2.RemoteEventBackfill)
		converted, err := t.convertToMatrix(ctx, portal, intent, message)
		if err != nil {
			return nil, err
		}
		reactionsList, _, customEmojis, err := t.computeReactionsList(ctx, message)
		if err != nil {
			return nil, err
		}

		backfillMessage := bridgev2.BackfillMessage{
			ConvertedMessage: converted,
			Sender:           sender,
			ID:               ids.GetMessageIDFromMessage(message),
			Timestamp:        time.Unix(int64(message.Date), 0),
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
				Sender: bridgev2.EventSender{
					IsFromMe:    reaction.My,
					SenderLogin: ids.MakeUserLoginID(peer.UserID),
					Sender:      ids.MakeUserID(peer.UserID),
				},
				EmojiID: emojiID,
				Emoji:   emoji,
			})
		}

		backfillMessages = append(backfillMessages, &backfillMessage)
	}

	// They are returned with most recent message first, so reverse the order.
	slices.Reverse(backfillMessages)

	return &bridgev2.FetchMessagesResponse{
		Messages: backfillMessages,
		Cursor:   cursor,
		HasMore:  len(messages) == fetchParams.Count,
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
