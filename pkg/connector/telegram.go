package connector

import (
	"bytes"
	"context"
	"fmt"
	"time"

	"github.com/gotd/td/tg"
	"github.com/rs/zerolog"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/bridgev2/simplevent"

	"go.mau.fi/mautrix-telegram/pkg/connector/emojis"
	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/connector/media"
	"go.mau.fi/mautrix-telegram/pkg/connector/tljson"
	"go.mau.fi/mautrix-telegram/pkg/connector/util"
)

type IGetMessage interface {
	GetMessage() tg.MessageClass
}

type IGetMessages interface {
	GetMessages() []int
}

func (t *TelegramClient) onUpdateNewMessage(ctx context.Context, update IGetMessage) error {
	log := zerolog.Ctx(ctx)
	switch msg := update.GetMessage().(type) {
	case *tg.Message:
		sender := t.getEventSender(msg)

		go t.handleTelegramReactions(ctx, msg)

		if media, ok := msg.GetMedia(); ok && media.TypeID() == tg.MessageMediaContactTypeID {
			contact := media.(*tg.MessageMediaContact)
			// TODO update the corresponding puppet
			log.Info().Int64("user_id", contact.UserID).Msg("received contact")
		}

		t.main.Bridge.QueueRemoteEvent(t.userLogin, &bridgev2.SimpleRemoteEvent[*tg.Message]{
			Type: bridgev2.RemoteEventMessage,
			LogContext: func(c zerolog.Context) zerolog.Context {
				return c.
					Int("message_id", msg.GetID()).
					Str("sender", string(sender.Sender)).
					Str("sender_login", string(sender.SenderLogin)).
					Bool("is_from_me", sender.IsFromMe)
			},
			ID:                 ids.MakeMessageID(msg.ID),
			Sender:             sender,
			PortalKey:          ids.MakePortalKey(msg.PeerID),
			Data:               msg,
			CreatePortal:       true,
			ConvertMessageFunc: t.convertToMatrix,
			Timestamp:          time.Unix(int64(msg.Date), 0),
		})
	case *tg.MessageService:
		// fmt.Printf("message service\n")
		// fmt.Printf("%v\n", msg)

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

func (t *TelegramClient) getEventSender(msg interface {
	GetOut() bool
	GetFromID() (tg.PeerClass, bool)
	GetPeerID() tg.PeerClass
}) bridgev2.EventSender {
	if msg.GetOut() {
		return bridgev2.EventSender{IsFromMe: true, SenderLogin: t.loginID, Sender: t.userID}
	} else if f, ok := msg.GetFromID(); ok && f.TypeID() == tg.PeerUserTypeID {
		from := f.(*tg.PeerUser)
		return bridgev2.EventSender{
			SenderLogin: ids.MakeUserLoginID(from.UserID),
			Sender:      ids.MakeUserID(from.UserID),
		}
	} else if peer, ok := msg.GetPeerID().(*tg.PeerUser); ok {
		return bridgev2.EventSender{
			SenderLogin: ids.MakeUserLoginID(peer.UserID),
			Sender:      ids.MakeUserID(peer.UserID),
		}
	} else {
		panic(fmt.Sprintf("couldn't determine sender (from: %+v) (peer: %+v)", f, msg.GetPeerID()))
	}
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

func (t *TelegramClient) onDeleteMessages(ctx context.Context, update IGetMessages) error {
	for _, messageID := range update.GetMessages() {
		parts, err := t.main.Bridge.DB.Message.GetAllPartsByID(ctx, t.loginID, ids.MakeMessageID(messageID))
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

func (t *TelegramClient) updateGhost(ctx context.Context, userID int64, user *tg.User) error {
	ghost, err := t.main.Bridge.GetGhostByID(ctx, ids.MakeUserID(userID))
	if err != nil {
		return err
	}
	userInfo, err := t.getUserInfoFromTelegramUser(user)
	if err != nil {
		return err
	}
	ghost.UpdateInfo(ctx, userInfo)
	return nil
}

func (t *TelegramClient) updateGhostWithUserInfo(ctx context.Context, userID int64, userInfo *bridgev2.UserInfo) error {
	ghost, err := t.main.Bridge.GetGhostByID(ctx, ids.MakeUserID(userID))
	if err != nil {
		return err
	}
	ghost.UpdateInfo(ctx, userInfo)
	return nil
}

func (t *TelegramClient) onEntityUpdate(ctx context.Context, e tg.Entities) error {
	for userID, user := range e.Users {
		t.updateGhost(ctx, userID, user)
	}
	return nil
}

func (t *TelegramClient) onMessageEdit(ctx context.Context, update IGetMessage) error {
	msg, ok := update.GetMessage().(*tg.Message)
	if !ok {
		return fmt.Errorf("edit message is not *tg.Message")
	}

	t.handleTelegramReactions(ctx, msg)

	sender := t.getEventSender(msg)

	t.main.Bridge.QueueRemoteEvent(t.userLogin, &bridgev2.SimpleRemoteEvent[*tg.Message]{
		Type: bridgev2.RemoteEventEdit,
		LogContext: func(c zerolog.Context) zerolog.Context {
			return c.
				Str("action", "edit_message").
				Str("conversion_direction", "to_matrix").
				Int("message_id", msg.ID)
		},
		ID:            ids.MakeMessageID(msg.ID),
		Sender:        sender,
		PortalKey:     ids.MakePortalKey(msg.PeerID),
		TargetMessage: ids.MakeMessageID(msg.ID),
		Data:          msg,
		Timestamp:     time.Unix(int64(msg.EditDate), 0),
		ConvertEditFunc: func(ctx context.Context, portal *bridgev2.Portal, intent bridgev2.MatrixAPI, existing []*database.Message, data *tg.Message) (*bridgev2.ConvertedEdit, error) {
			converted, err := t.convertToMatrix(ctx, portal, intent, msg)
			if err != nil {
				return nil, err
			} else if len(existing) != len(converted.Parts) {
				return nil, fmt.Errorf("parts were added or removed in edit")
			}

			var ce bridgev2.ConvertedEdit
			for i, part := range converted.Parts {
				if !bytes.Equal(existing[i].Metadata.(*MessageMetadata).ContentHash, part.DBMetadata.(*MessageMetadata).ContentHash) {
					ce.ModifiedParts = append(ce.ModifiedParts, part.ToEditPart(existing[i]))
				}
			}
			return &ce, nil
		},
	})

	return nil
}

func (t *TelegramClient) handleTelegramReactions(ctx context.Context, msg *tg.Message) {
	log := zerolog.Ctx(ctx).With().
		Str("handler", "handle_telegram_reactions").
		Int("message_id", msg.ID).
		Logger()

	if _, set := msg.GetReactions(); !set {
		log.Debug().Msg("no reactions set on message")
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

	dbMsg, err := t.main.Bridge.DB.Message.GetFirstPartByID(ctx, t.loginID, ids.MakeMessageID(msg.ID))
	if err != nil {
		log.Err(err).Msg("failed to get message from database")
		return
	} else if dbMsg == nil {
		log.Warn().Msg("no message found in database")
		return
	}

	if len(reactionsList) < totalCount {
		if user, ok := msg.PeerID.(*tg.PeerUser); ok {
			reactionsList = splitDMReactionCounts(msg.Reactions.Results, user.UserID, t.telegramUserID)

			// TODO
			// } else if t.isBot {
			// 	// Can't fetch exact reaction senders as a bot
			// 	return

			// TODO should calls to this be limited?
		} else if peer, err := t.inputPeerForPortalID(ctx, ids.MakePortalKey(msg.PeerID).ID); err != nil {
			log.Err(err).Msg("failed to get input peer")
			return
		} else {
			reactions, err := t.client.API().MessagesGetMessageReactionsList(ctx, &tg.MessagesGetMessageReactionsListRequest{
				Peer: peer, ID: msg.ID, Limit: 100,
			})
			if err != nil {
				log.Err(err).Msg("failed to get reactions list")
				return
			}
			reactionsList = reactions.Reactions
		}
	}

	var customEmojiIDs []int64
	for _, reaction := range reactionsList {
		if e, ok := reaction.Reaction.(*tg.ReactionCustomEmoji); ok {
			customEmojiIDs = append(customEmojiIDs, e.DocumentID)
		} else if reaction.Reaction.TypeID() != tg.ReactionEmojiTypeID {
			log.Error().Type("reaction", reaction.Reaction).Msg("unknown reaction type")
			return
		}
	}

	customEmojis, err := t.transferEmojisToMatrix(ctx, customEmojiIDs)
	if err != nil {
		log.Err(err).Msg("failed to transfer emojis")
		return
	}

	isFull := len(reactionsList) == totalCount
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

		var emojiID networkid.EmojiID
		var emoji string
		if r, ok := reaction.Reaction.(*tg.ReactionCustomEmoji); ok {
			emojiID = ids.MakeEmojiIDFromDocumentID(r.DocumentID)
			emoji = customEmojis[emojiID]
		} else if r, ok := reaction.Reaction.(*tg.ReactionEmoji); ok {
			emojiID = ids.MakeEmojiIDFromEmoticon(r.Emoticon)
			emoji = r.Emoticon
		} else {
			log.Error().Type("reaction_type", reaction.Reaction).Msg("invalid reaction type")
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
				return c.Str("message_id", string(msg.ID))
			},
			PortalKey: dbMsg.Room,
		},
		TargetMessage: dbMsg.ID,
		Reactions:     &bridgev2.ReactionSyncData{Users: users, HasAllUsers: isFull},
	})
}

func (t *TelegramClient) inputPeerForPortalID(ctx context.Context, portalID networkid.PortalID) (tg.InputPeerClass, error) {
	peerType, id, err := ids.ParsePortalID(portalID)
	if err != nil {
		return nil, err
	}
	switch peerType {
	case ids.PeerTypeUser:
		return &tg.InputPeerUser{UserID: id}, nil
	case ids.PeerTypeChat:
		return &tg.InputPeerChat{ChatID: id}, nil
	case ids.PeerTypeChannel:
		accessHash, found, err := t.ScopedStore.GetChannelAccessHash(ctx, t.telegramUserID, id)
		if err != nil {
			return nil, err
		} else if !found {
			return nil, fmt.Errorf("channel access hash not found for %d", id)
		}
		return &tg.InputPeerChannel{ChannelID: id, AccessHash: accessHash}, nil
	default:
		panic("invalid peer type")
	}
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

func (t *TelegramClient) getAppConfigCached(ctx context.Context) (map[string]any, error) {
	if t.appConfig == nil {
		cfg, err := t.client.API().HelpGetAppConfig(ctx, t.appConfigHash)
		if err != nil {
			return nil, err
		}
		appConfig, ok := cfg.(*tg.HelpAppConfig)
		if !ok {
			return nil, fmt.Errorf("failed to get app config: unexpected type %T", appConfig)
		}
		parsedConfig, err := tljson.Parse(appConfig.Config)
		if err != nil {
			return nil, err
		}
		t.appConfig, ok = parsedConfig.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("failed to parse app config: unexpected type %T", t.appConfig)
		}
		t.appConfigHash = appConfig.Hash
	}
	return t.appConfig, nil
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

func (t *TelegramClient) transferEmojisToMatrix(ctx context.Context, customEmojiIDs []int64) (result map[networkid.EmojiID]string, err error) {
	result, customEmojiIDs = emojis.ConvertKnownEmojis(customEmojiIDs)

	if len(customEmojiIDs) == 0 {
		return
	}

	customEmojiDocuments, err := t.client.API().MessagesGetCustomEmojiDocuments(ctx, customEmojiIDs)
	if err != nil {
		return nil, err
	}

	for _, customEmojiDocument := range customEmojiDocuments {
		mxcURI, _, _, err := media.NewTransferer(t.client.API()).
			WithStickerConfig(t.main.Config.AnimatedSticker).
			WithDocument(customEmojiDocument, false).
			Transfer(ctx, t.main.Store, t.main.Bridge.Bot)
		if err != nil {
			return nil, err
		}
		result[ids.MakeEmojiIDFromDocumentID(customEmojiDocument.GetID())] = string(mxcURI)
	}
	return
}
