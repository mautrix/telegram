package connector

import (
	"context"
	"fmt"
	"slices"
	"sync"
	"time"

	"github.com/gotd/td/tg"
	"github.com/rs/zerolog"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/networkid"

	"go.mau.fi/mautrix-telegram/pkg/connector/emojis"
	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/connector/media"
	"go.mau.fi/mautrix-telegram/pkg/connector/util"
)

func (t *TelegramClient) onUpdateNewMessage(ctx context.Context, e tg.Entities, update *tg.UpdateNewMessage) error {
	log := zerolog.Ctx(ctx)
	switch msg := update.GetMessage().(type) {
	case *tg.Message:
		sender := t.getEventSender(msg)

		if err := t.handleTelegramReactions(ctx, msg); err != nil {
			return err
		}

		if media, ok := msg.GetMedia(); ok && media.TypeID() == tg.MessageMediaContactTypeID {
			contact := media.(*tg.MessageMediaContact)
			// TODO update the corresponding puppet
			log.Info().Int64("user_id", contact.UserID).Msg("received contact")
		}

		t.main.Bridge.QueueRemoteEvent(t.userLogin, &bridgev2.SimpleRemoteEvent[*tg.Message]{
			Type: bridgev2.RemoteEventMessage,
			LogContext: func(c zerolog.Context) zerolog.Context {
				return c.
					Int("message_id", update.Message.GetID()).
					Str("sender", string(sender.Sender)).
					Str("sender_login", string(sender.SenderLogin)).
					Bool("is_from_me", sender.IsFromMe)
			},
			ID:                 ids.MakeMessageID(msg.ID),
			Sender:             sender,
			PortalKey:          ids.MakePortalKey(msg.PeerID),
			Data:               msg,
			CreatePortal:       true,
			ConvertMessageFunc: t.msgConv.ToMatrix,
			Timestamp:          time.Unix(int64(msg.Date), 0),
		})
	case *tg.MessageService:
		fmt.Printf("message service\n")
		fmt.Printf("%v\n", msg)

		// sender := t.getEventSender(msg)
		// switch action := msg.Action.(type) {
		// case *tg.MessageActionChatEditTitle:
		// case *tg.MessageActionChatCreate:
		// case *tg.MessageActionChatEditPhoto:
		// case *tg.MessageActionChatDeletePhoto:
		// case *tg.MessageActionChatAddUser:
		// case *tg.MessageActionChatDeleteUser:
		// case *tg.MessageActionChatJoinedByLink:
		// case *tg.MessageActionChannelCreate:
		// case *tg.MessageActionChatMigrateTo:
		// case *tg.MessageActionChannelMigrateFrom:
		// case *tg.MessageActionPinMessage:
		// case *tg.MessageActionHistoryClear:
		// case *tg.MessageActionGameScore:
		// case *tg.MessageActionPaymentSentMe:
		// case *tg.MessageActionPaymentSent:
		// case *tg.MessageActionPhoneCall:
		// case *tg.MessageActionScreenshotTaken:
		// case *tg.MessageActionCustomAction:
		// case *tg.MessageActionBotAllowed:
		// case *tg.MessageActionSecureValuesSentMe:
		// case *tg.MessageActionSecureValuesSent:
		// case *tg.MessageActionContactSignUp:
		// case *tg.MessageActionGeoProximityReached:
		// case *tg.MessageActionGroupCall:
		// case *tg.MessageActionInviteToGroupCall:
		// case *tg.MessageActionSetMessagesTTL:
		// case *tg.MessageActionGroupCallScheduled:
		// case *tg.MessageActionSetChatTheme:
		// case *tg.MessageActionChatJoinedByRequest:
		// case *tg.MessageActionWebViewDataSentMe:
		// case *tg.MessageActionWebViewDataSent:
		// case *tg.MessageActionGiftPremium:
		// case *tg.MessageActionTopicCreate:
		// case *tg.MessageActionTopicEdit:
		// case *tg.MessageActionSuggestProfilePhoto:
		// case *tg.MessageActionRequestedPeer:
		// case *tg.MessageActionSetChatWallPaper:
		// case *tg.MessageActionGiftCode:
		// case *tg.MessageActionGiveawayLaunch:
		// case *tg.MessageActionGiveawayResults:
		// case *tg.MessageActionBoostApply:
		// case *tg.MessageActionRequestedPeerSentMe:
		// default:
		// 	return fmt.Errorf("unknown action type %T", action)
		// }

	default:
		return fmt.Errorf("unknown message type %T", msg)
	}
	return nil
}

type messageWithSender interface {
	GetOut() bool
	GetFromID() (tg.PeerClass, bool)
	GetPeerID() tg.PeerClass
}

func (t *TelegramClient) getEventSender(msg messageWithSender) (sender bridgev2.EventSender) {
	if msg.GetOut() {
		sender.IsFromMe = true
		sender.SenderLogin = ids.MakeUserLoginID(t.loginID)
		sender.Sender = ids.MakeUserID(t.loginID)
	} else if f, ok := msg.GetFromID(); ok {
		switch from := f.(type) {
		case *tg.PeerUser:
			sender.SenderLogin = ids.MakeUserLoginID(from.UserID)
			sender.Sender = ids.MakeUserID(from.UserID)
		default:
			fmt.Printf("%+v\n", f)
			fmt.Printf("%T\n", f)
			panic("unimplemented FromID")
		}
	} else if peer, ok := msg.GetPeerID().(*tg.PeerUser); ok {
		sender.SenderLogin = ids.MakeUserLoginID(peer.UserID)
		sender.Sender = ids.MakeUserID(peer.UserID)
	} else {
		panic("not from anyone")
	}
	return
}

func (t *TelegramClient) onUpdateNewChannelMessage(ctx context.Context, e tg.Entities, update *tg.UpdateNewChannelMessage) error {
	fmt.Printf("update new channel message %+v\n", update)
	return nil
}

func (t *TelegramClient) onUserName(ctx context.Context, e tg.Entities, update *tg.UpdateUserName) error {
	ghost, err := t.main.Bridge.GetGhostByID(ctx, ids.MakeUserID(update.UserID))
	if err != nil {
		return err
	}

	name := util.FormatFullName(update.FirstName, update.LastName)

	// TODO update identifiers?
	ghost.UpdateInfo(ctx, &bridgev2.UserInfo{Name: &name})
	return nil
}

func (t *TelegramClient) onDeleteMessages(ctx context.Context, e tg.Entities, update *tg.UpdateDeleteMessages) error {
	for _, messageID := range update.Messages {
		parts, err := t.main.Bridge.DB.Message.GetAllPartsByID(ctx, ids.MakeUserLoginID(t.loginID), ids.MakeMessageID(messageID))
		if err != nil {
			return err
		}
		if len(parts) == 0 {
			return fmt.Errorf("no parts found for message %d", messageID)
		}
		t.main.Bridge.QueueRemoteEvent(t.userLogin, &bridgev2.SimpleRemoteEvent[any]{
			Type: bridgev2.RemoteEventMessageRemove,
			LogContext: func(c zerolog.Context) zerolog.Context {
				return c.
					Str("action", "delete message").
					Int("message_id", messageID)
			},
			PortalKey:     parts[0].Room,
			CreatePortal:  false,
			TargetMessage: ids.MakeMessageID(messageID),
		})
	}
	return nil
}

func (t *TelegramClient) onEntityUpdate(ctx context.Context, e tg.Entities) error {
	for userID, user := range e.Users {
		ghost, err := t.main.Bridge.GetGhostByID(ctx, ids.MakeUserID(userID))
		if err != nil {
			return err
		}
		userInfo, err := t.getUserInfoFromTelegramUser(user)
		if err != nil {
			return err
		}
		ghost.UpdateInfo(ctx, userInfo)
	}
	return nil
}

func (t *TelegramClient) onMessageEdit(ctx context.Context, e tg.Entities, update *tg.UpdateEditMessage) error {
	fmt.Printf("message edit %+v\n", update)
	msg, ok := update.Message.(*tg.Message)
	if !ok {
		return fmt.Errorf("edit message is not *tg.Message")
	}

	if err := t.handleTelegramReactions(ctx, msg); err != nil {
		return err
	}

	// t.main.Bridge.QueueRemoteEvent(t.userLogin, &bridgev2.SimpleRemoteEvent[*tg.Message]{
	// 	Type: bridgev2.RemoteEventEdit,
	// 	LogContext: func(c zerolog.Context) zerolog.Context {
	// 		return c.
	// 			Str("action", "edit message").
	// 			Int("message_id", message.ID)
	// 	},
	// 	Sender:    sender,
	// 	PortalKey: ids.MakePortalID(message.PeerID),
	// })

	return nil
}

func (t *TelegramClient) handleTelegramReactions(ctx context.Context, msg *tg.Message) error {
	if _, set := msg.GetReactions(); !set {
		return nil
	}
	var totalCount int
	for _, r := range msg.Reactions.Results {
		totalCount += r.Count
	}

	reactionsList := msg.Reactions.RecentReactions
	if totalCount > 0 && len(reactionsList) == 0 && !msg.Reactions.CanSeeList {
		// We don't know who reacted in a channel, so we can't bridge it properly either
		return nil
	}

	// TODO
	// if self.peer_type == "channel" and not self.megagroup:
	//     # This should never happen with the previous if
	//     self.log.warning(f"Can see reaction list in channel ({data!s})")
	//     # return

	dbMsg, err := t.main.Bridge.DB.Message.GetFirstPartByID(ctx, ids.MakeUserLoginID(t.loginID), ids.MakeMessageID(msg.ID))
	if err != nil {
		return err
	} else if dbMsg == nil {
		return fmt.Errorf("no message found with ID %d", msg.ID)
	}

	if len(reactionsList) < totalCount {
		if user, ok := msg.PeerID.(*tg.PeerUser); ok {
			reactionsList = splitDMReactionCounts(msg.Reactions.Results, user.UserID, t.loginID)

			// TODO
			// } else if t.isBot {
			// 	// Can't fetch exact reaction senders as a bot
			// 	return

			// TODO should calls to this be limited?
		} else if peer, err := ids.InputPeerForPortalKey(ids.MakePortalKey(msg.PeerID)); err != nil {
			return err
		} else {
			reactions, err := t.client.API().MessagesGetMessageReactionsList(ctx, &tg.MessagesGetMessageReactionsListRequest{
				Peer: peer, ID: msg.ID, Limit: 100,
			})
			if err != nil {
				return err
			}
			reactionsList = reactions.Reactions
		}
	}

	if _, ok := t.reactionMessageLocks[msg.ID]; !ok {
		t.reactionMessageLocks[msg.ID] = &sync.Mutex{}
	}
	t.reactionMessageLocks[msg.ID].Lock()
	defer t.reactionMessageLocks[msg.ID].Unlock()

	isFull := len(reactionsList) == totalCount
	reactions := map[networkid.UserID][]tg.MessagePeerReaction{}
	var customEmojiIDs []int64
	for _, reaction := range reactionsList {
		if e, ok := reaction.Reaction.(*tg.ReactionCustomEmoji); ok {
			customEmojiIDs = append(customEmojiIDs, e.DocumentID)
		} else if reaction.Reaction.TypeID() != tg.ReactionEmojiTypeID {
			return fmt.Errorf("unknown reaction type %T", reaction.Reaction)
		}

		if p, ok := reaction.PeerID.(*tg.PeerUser); !ok {
			return fmt.Errorf("reaction peer ID is not a user")
		} else {
			reactions[ids.MakeUserID(p.UserID)] = append(reactions[ids.MakeUserID(p.UserID)], reaction)
		}
	}

	return t.handleTelegramParsedReactionsLocked(ctx, dbMsg, reactions, customEmojiIDs, isFull, nil, nil)
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

func (t *TelegramClient) getReactionLimit(ctx context.Context, sender networkid.UserID) (int, error) {
	// TODO implement this correctly (probably need to put something into metadata)
	// ghost, err := t.main.Bridge.GetGhostByID(ctx, sender)
	// if err != nil {
	// 	return 0, err
	// }
	return 1, nil
}

func (t *TelegramClient) transferEmojisToMatrix(ctx context.Context, customEmojiIDs []int64) (result map[networkid.EmojiID]string, err error) {
	result, customEmojiIDs = emojis.ConvertKnownEmojis(customEmojiIDs)

	if len(customEmojiIDs) > 0 {
		customEmojiDocuments, err := t.client.API().MessagesGetCustomEmojiDocuments(ctx, customEmojiIDs)
		if err != nil {
			return nil, err
		}

		for _, customEmojiDocument := range customEmojiDocuments {
			document := customEmojiDocument.(*tg.Document)
			mxcURI, _, _, _, err := media.NewTransferer(t.main.Config.AnimatedSticker).
				WithIsSticker(true).
				Transfer(ctx, t.main.Store, t.client.API(), t.main.Bridge.Bot, &tg.InputDocumentFileLocation{
					ID:            document.GetID(),
					AccessHash:    document.GetAccessHash(),
					FileReference: document.GetFileReference(),
				})
			if err != nil {
				return nil, err
			}
			result[ids.MakeEmojiIDFromDocumentID(document.ID)] = string(mxcURI)
		}
	}
	return
}

func (t *TelegramClient) handleTelegramParsedReactionsLocked(ctx context.Context, msg *database.Message, reactions map[networkid.UserID][]tg.MessagePeerReaction, customEmojiIDs []int64, isFull bool, onlyUserID *networkid.UserID, timestamp *time.Time) error {
	customEmojis, err := t.transferEmojisToMatrix(ctx, customEmojiIDs)
	if err != nil {
		return err
	}

	existingReactions, err := t.main.Bridge.DB.Reaction.GetAllToMessage(ctx, msg.ID)
	if err != nil {
		return err
	}

	var removed []*database.Reaction
	for _, existing := range existingReactions {
		if onlyUserID != nil && existing.SenderID != *onlyUserID {
			continue
		}
		var matched bool
		reactions[existing.SenderID], matched, err = reactionsFilter(reactions[existing.SenderID], existing)
		if err != nil {
			return err
		} else if !matched {
			if isFull {
				removed = append(removed, existing)
			} else if reactionLimit, err := t.getReactionLimit(ctx, existing.SenderID); err != nil {
				return err
			} else if len(reactions[existing.SenderID]) >= reactionLimit {
				removed = append(removed, existing)
			}
		}
	}

	for sender, reactions := range reactions {
		senderID, err := ids.ParseUserID(sender)
		if err != nil {
			return err
		}

		for _, reaction := range reactions {
			var emojiID networkid.EmojiID
			var emoji string
			if r, ok := reaction.Reaction.(*tg.ReactionCustomEmoji); ok {
				emojiID = ids.MakeEmojiIDFromDocumentID(r.DocumentID)
				emoji = customEmojis[emojiID]
			} else if r, ok := reaction.Reaction.(*tg.ReactionEmoji); ok {
				emojiID = ids.MakeEmojiIDFromEmoticon(r.Emoticon)
				emoji = r.Emoticon
			} else {
				return fmt.Errorf("invalid reaction type %T", reaction.Reaction)
			}

			evt := &bridgev2.SimpleRemoteEvent[any]{
				Type: bridgev2.RemoteEventReaction,
				LogContext: func(c zerolog.Context) zerolog.Context {
					return c.
						Any("reaction", reaction.Reaction).
						Str("sender_id", string(sender)).
						Str("message_id", string(msg.ID))
				},
				Sender: bridgev2.EventSender{
					IsFromMe:    reaction.My,
					SenderLogin: ids.MakeUserLoginID(senderID),
					Sender:      sender,
				},
				PortalKey:     msg.Room,
				TargetMessage: msg.ID,
				EmojiID:       emojiID,
				Emoji:         emoji,
			}
			if timestamp != nil {
				evt.Timestamp = *timestamp
			}
			t.main.Bridge.QueueRemoteEvent(t.userLogin, evt)
		}
	}

	for _, r := range removed {
		senderID, err := ids.ParseUserID(r.SenderID)
		if err != nil {
			return err
		}
		evt := &bridgev2.SimpleRemoteEvent[any]{
			Type: bridgev2.RemoteEventReactionRemove,
			LogContext: func(c zerolog.Context) zerolog.Context {
				return c.
					Str("emoji_id", string(r.EmojiID)).
					Str("sender_id", string(r.SenderID)).
					Str("message_id", string(msg.ID))
			},
			Sender: bridgev2.EventSender{
				IsFromMe:    t.loginID == senderID,
				SenderLogin: ids.MakeUserLoginID(senderID),
				Sender:      r.SenderID,
			},
			PortalKey:     msg.Room,
			TargetMessage: r.MessageID,
			EmojiID:       r.EmojiID,
		}
		if timestamp != nil {
			evt.Timestamp = *timestamp
		}
		t.main.Bridge.QueueRemoteEvent(t.userLogin, evt)
	}

	return nil
}

func reactionsFilter(reactions []tg.MessagePeerReaction, existing *database.Reaction) (newReactions []tg.MessagePeerReaction, matched bool, err error) {
	if len(reactions) == 0 {
		return nil, false, nil
	}

	documentID, emoticon, err := ids.ParseEmojiID(existing.EmojiID)
	if err != nil {
		return nil, false, err
	}

	newReactions = slices.DeleteFunc(reactions, func(r tg.MessagePeerReaction) bool {
		if rce, ok := r.Reaction.(*tg.ReactionCustomEmoji); ok {
			return documentID == rce.DocumentID
		} else if r, ok := r.Reaction.(*tg.ReactionEmoji); ok {
			return emoticon == r.Emoticon
		}
		return false
	})
	return newReactions, len(newReactions) < len(reactions), nil
}
