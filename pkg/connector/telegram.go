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
	"maunium.net/go/mautrix/event"

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

		t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.Message[*tg.Message]{
			EventMeta: simplevent.EventMeta{
				Type: bridgev2.RemoteEventMessage,
				LogContext: func(c zerolog.Context) zerolog.Context {
					return c.
						Int("message_id", msg.GetID()).
						Str("sender", string(sender.Sender)).
						Str("sender_login", string(sender.SenderLogin)).
						Bool("is_from_me", sender.IsFromMe).
						Stringer("peer_id", msg.PeerID)
				},
				Sender:       sender,
				PortalKey:    ids.MakePortalKey(msg.PeerID, t.loginID),
				CreatePortal: true,
				Timestamp:    time.Unix(int64(msg.Date), 0),
			},
			ID:                 ids.GetMessageIDFromMessage(msg),
			Data:               msg,
			ConvertMessageFunc: t.convertToMatrix,
		})
	case *tg.MessageService:
		sender := t.getEventSender(msg)

		chatInfoChange := simplevent.ChatInfoChange{
			EventMeta: simplevent.EventMeta{
				Type:      bridgev2.RemoteEventChatInfoChange,
				PortalKey: ids.MakePortalKey(msg.PeerID, t.loginID),
				Sender:    sender,
				Timestamp: time.Unix(int64(msg.Date), 0),
				LogContext: func(c zerolog.Context) zerolog.Context {
					return c.
						Int("message_id", msg.GetID()).
						Str("sender", string(sender.Sender)).
						Str("sender_login", string(sender.SenderLogin)).
						Bool("is_from_me", sender.IsFromMe).
						Stringer("peer_id", msg.PeerID)
				},
			},
			ChatInfoChange: &bridgev2.ChatInfoChange{},
		}

		switch action := msg.Action.(type) {
		case *tg.MessageActionChatEditTitle:
			chatInfoChange.ChatInfoChange.ChatInfo = &bridgev2.ChatInfo{Name: &action.Title}
		case *tg.MessageActionChatEditPhoto:
			chatInfoChange.ChatInfoChange.ChatInfo = &bridgev2.ChatInfo{Avatar: t.avatarFromPhoto(action.Photo)}
		case *tg.MessageActionChatDeletePhoto:
			chatInfoChange.ChatInfoChange.ChatInfo = &bridgev2.ChatInfo{Avatar: &bridgev2.Avatar{Remove: true}}
		case *tg.MessageActionChatAddUser:
			chatInfoChange.ChatInfoChange.MemberChanges = &bridgev2.ChatMemberList{
				MemberMap: map[networkid.UserID]bridgev2.ChatMember{},
			}
			for _, userID := range action.Users {
				sender := ids.MakeUserID(userID)
				chatInfoChange.ChatInfoChange.MemberChanges.MemberMap[sender] = bridgev2.ChatMember{
					EventSender: bridgev2.EventSender{
						SenderLogin: ids.MakeUserLoginID(userID),
						Sender:      sender,
					},
					Membership: event.MembershipJoin,
				}
			}
		case *tg.MessageActionChatJoinedByLink:
			chatInfoChange.ChatInfoChange.MemberChanges = &bridgev2.ChatMemberList{
				MemberMap: map[networkid.UserID]bridgev2.ChatMember{
					sender.Sender: {EventSender: sender, Membership: event.MembershipJoin},
				},
			}
		case *tg.MessageActionChatDeleteUser:
			sender := ids.MakeUserID(action.UserID)
			chatInfoChange.ChatInfoChange.MemberChanges = &bridgev2.ChatMemberList{
				MemberMap: map[networkid.UserID]bridgev2.ChatMember{
					sender: {
						EventSender: bridgev2.EventSender{
							SenderLogin: ids.MakeUserLoginID(action.UserID),
							Sender:      sender,
						},
						Membership: event.MembershipLeave,
					},
				},
			}
		// case *tg.MessageActionChatCreate:
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
		default:
			return fmt.Errorf("unknown action type %T", action)
		}
		t.main.Bridge.QueueRemoteEvent(t.userLogin, &chatInfoChange)

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
		return t.mySender()
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

func (t *TelegramClient) onDeleteMessages(ctx context.Context, channelID int64, update IGetMessages) error {
	for _, messageID := range update.GetMessages() {
		var portalKey networkid.PortalKey
		if channelID == 0 {
			// TODO have mautrix-go do this part too?
			parts, err := t.main.Bridge.DB.Message.GetAllPartsByID(ctx, t.loginID, ids.MakeMessageID(channelID, messageID))
			if err != nil {
				return err
			}
			if len(parts) == 0 {
				return fmt.Errorf("no parts found for message %d", messageID)
			}
			// TODO can deletes happen across rooms?
			portalKey = parts[0].Room
		} else {
			portalKey = ids.MakePortalKey(&tg.PeerChannel{ChannelID: channelID}, t.loginID)
		}
		t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.MessageRemove{
			EventMeta: simplevent.EventMeta{
				Type: bridgev2.RemoteEventMessageRemove,
				LogContext: func(c zerolog.Context) zerolog.Context {
					return c.
						Str("action", "delete message").
						Int("message_id", messageID)
				},
				PortalKey:    portalKey,
				CreatePortal: false,
			},
			TargetMessage: ids.MakeMessageID(channelID, messageID),
		})
	}
	return nil
}

func (t *TelegramClient) updateGhost(ctx context.Context, userID int64, user *tg.User) error {
	ghost, err := t.main.Bridge.GetGhostByID(ctx, ids.MakeUserID(userID))
	if err != nil {
		return err
	}
	userInfo, err := t.getUserInfoFromTelegramUser(ctx, user)
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

	t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.Message[*tg.Message]{
		EventMeta: simplevent.EventMeta{
			Type: bridgev2.RemoteEventEdit,
			LogContext: func(c zerolog.Context) zerolog.Context {
				return c.
					Str("action", "edit_message").
					Str("conversion_direction", "to_matrix").
					Int("message_id", msg.ID)
			},
			Sender:    sender,
			PortalKey: ids.MakePortalKey(msg.PeerID, t.loginID),
			Timestamp: time.Unix(int64(msg.EditDate), 0),
		},
		ID:            ids.GetMessageIDFromMessage(msg),
		TargetMessage: ids.GetMessageIDFromMessage(msg),
		Data:          msg,
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

func (t *TelegramClient) handleTyping(portal networkid.PortalKey, userID int64, action tg.SendMessageActionClass) error {
	if userID == t.telegramUserID {
		return nil
	}
	timeout := time.Duration(6) * time.Second
	if action.TypeID() != tg.SendMessageTypingActionTypeID {
		timeout = 0
	}
	// TODO send proper TypingTypes
	t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.Typing{
		EventMeta: simplevent.EventMeta{
			Type:      bridgev2.RemoteEventTyping,
			PortalKey: portal,
			Sender: bridgev2.EventSender{
				SenderLogin: ids.MakeUserLoginID(userID),
				Sender:      ids.MakeUserID(userID),
			},
		},
		Timeout: timeout,
	})
	return nil
}

func (t *TelegramClient) updateReadReceipt(update *tg.UpdateReadHistoryOutbox) error {
	user, ok := update.Peer.(*tg.PeerUser)
	if !ok {
		// Read receipts from other users are meaningless in chats/channels
		// (they only say "someone read the message" and not who)
		return nil
	}
	t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.Receipt{
		EventMeta: simplevent.EventMeta{
			Type:      bridgev2.RemoteEventReadReceipt,
			PortalKey: ids.MakePortalKey(update.Peer, t.loginID),
			Sender: bridgev2.EventSender{
				SenderLogin: ids.MakeUserLoginID(user.UserID),
				Sender:      ids.MakeUserID(user.UserID),
			},
		},
		LastTarget: ids.MakeMessageID(update.Peer, update.MaxID),
	})
	return nil
}

func (t *TelegramClient) onOwnReadReceipt(portalKey networkid.PortalKey, maxID int) error {
	t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.Receipt{
		EventMeta: simplevent.EventMeta{
			Type:      bridgev2.RemoteEventReadReceipt,
			PortalKey: portalKey,
			Sender:    t.mySender(),
		},
		LastTarget: ids.MakeMessageID(portalKey, maxID),
	})
	return nil
}

func (t *TelegramClient) inputPeerForPortalID(ctx context.Context, portalID networkid.PortalID) (tg.InputPeerClass, error) {
	peerType, id, err := ids.ParsePortalID(portalID)
	if err != nil {
		return nil, err
	}
	switch peerType {
	case ids.PeerTypeUser:
		if accessHash, err := t.ScopedStore.GetAccessHash(ctx, id); err != nil {
			return nil, fmt.Errorf("failed to get user access hash for %d: %w", id, err)
		} else {
			return &tg.InputPeerUser{UserID: id, AccessHash: accessHash}, nil
		}
	case ids.PeerTypeChat:
		return &tg.InputPeerChat{ChatID: id}, nil
	case ids.PeerTypeChannel:
		if accessHash, err := t.ScopedStore.GetAccessHash(ctx, id); err != nil {
			return nil, err
		} else {
			return &tg.InputPeerChannel{ChannelID: id, AccessHash: accessHash}, nil
		}
	default:
		panic("invalid peer type")
	}
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
