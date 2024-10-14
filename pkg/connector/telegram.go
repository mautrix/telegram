package connector

import (
	"bytes"
	"context"
	"fmt"
	"slices"
	"strings"
	"time"

	"github.com/gotd/td/tg"
	"github.com/gotd/td/tgerr"
	"github.com/rs/zerolog"
	"go.mau.fi/util/ptr"
	"golang.org/x/exp/maps"
	"maunium.net/go/mautrix/bridge/status"
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

func (t *TelegramClient) onUpdateChannel(ctx context.Context, update *tg.UpdateChannel) error {
	log := zerolog.Ctx(ctx).With().
		Str("handler", "on_update_channel").
		Int64("channel_id", update.ChannelID).
		Logger()
	log.Debug().Msg("Fetching channel due to UpdateChannel event")

	leave := func() {
		t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatDelete{
			EventMeta: simplevent.EventMeta{
				Type: bridgev2.RemoteEventChatDelete,
				LogContext: func(c zerolog.Context) zerolog.Context {
					return c.Int64("channel_id", update.ChannelID)
				},
				PortalKey: t.makePortalKeyFromID(ids.PeerTypeChannel, update.ChannelID),
				Sender:    t.mySender(),
			},
			OnlyForMe: true,
		})
	}

	chats, err := APICallWithOnlyChatUpdates(ctx, t, func() (tg.MessagesChatsClass, error) {
		if accessHash, err := t.ScopedStore.GetAccessHash(ctx, ids.PeerTypeChannel, update.ChannelID); err != nil {
			return nil, err
		} else {
			return t.client.API().ChannelsGetChannels(ctx, []tg.InputChannelClass{
				&tg.InputChannel{ChannelID: update.ChannelID, AccessHash: accessHash},
			})
		}
	})
	if err != nil {
		if tgerr.Is(err, tg.ErrChannelInvalid, tg.ErrChannelPrivate) {
			leave()
			return nil
		}
		return fmt.Errorf("failed to get channel: %w", err)
	} else if len(chats.GetChats()) != 1 {
		return fmt.Errorf("expected 1 chat, got %d", len(chats.GetChats()))
	} else if channel, ok := chats.GetChats()[0].(*tg.Channel); !ok {
		log.Error().Type("chat_type", chats.GetChats()[0]).Msg("Expected channel, got something else. Leaving the channel.")
		leave()
	} else if channel.Left {
		log.Error().Msg("Update was for a left channel. Leaving the channel.")
		leave()
	} else {
		// TODO update the channel info
	}
	return nil
}

func (t *TelegramClient) onUpdateNewMessage(ctx context.Context, channels map[int64]*tg.Channel, update IGetMessage) error {
	log := zerolog.Ctx(ctx)
	switch msg := update.GetMessage().(type) {
	case *tg.Message:
		sender := t.getEventSender(msg)

		if channel, ok := msg.PeerID.(*tg.PeerChannel); ok {
			if c, ok := channels[channel.ChannelID]; ok && c.Left {
				log.Debug().
					Int64("channel_id", channel.ChannelID).
					Msg("Received message in left channel, ignoring")
				return nil
			}
		}

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
				PortalKey:    t.makePortalKeyFromPeer(msg.PeerID),
				CreatePortal: true,
				Timestamp:    time.Unix(int64(msg.Date), 0),
			},
			ID:                 ids.GetMessageIDFromMessage(msg),
			Data:               msg,
			ConvertMessageFunc: t.convertToMatrix,
		})
	case *tg.MessageService:
		sender := t.getEventSender(msg)

		eventMeta := simplevent.EventMeta{
			Type:      bridgev2.RemoteEventChatInfoChange,
			PortalKey: t.makePortalKeyFromPeer(msg.PeerID),
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
		}
		chatInfoChange := simplevent.ChatInfoChange{
			EventMeta:      eventMeta,
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
			if action.UserID == t.telegramUserID {
				eventMeta.Type = bridgev2.RemoteEventChatDelete
				t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatDelete{
					EventMeta: eventMeta,
					OnlyForMe: true,
				})
				return nil
			}
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
		case *tg.MessageActionChatCreate:
			memberMap := map[networkid.UserID]bridgev2.ChatMember{}
			for _, userID := range action.Users {
				memberMap[ids.MakeUserID(userID)] = bridgev2.ChatMember{
					EventSender: t.senderForUserID(userID),
					Membership:  event.MembershipJoin,
				}
			}

			eventMeta.Type = bridgev2.RemoteEventChatResync
			eventMeta.CreatePortal = true
			t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatResync{
				EventMeta: eventMeta,
				ChatInfo: &bridgev2.ChatInfo{
					Name: &action.Title,
					Members: &bridgev2.ChatMemberList{
						IsFull:           true,
						TotalMemberCount: len(action.Users),
						MemberMap:        memberMap,
					},
					CanBackfill: true,
				},
			})

		case *tg.MessageActionChannelCreate:
			eventMeta.Type = bridgev2.RemoteEventChatResync
			eventMeta.CreatePortal = true
			modLevel := 50
			t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatResync{
				EventMeta: eventMeta,
				ChatInfo: &bridgev2.ChatInfo{
					Name: &action.Title,
					Members: &bridgev2.ChatMemberList{
						MemberMap: map[networkid.UserID]bridgev2.ChatMember{
							t.userID: {
								EventSender: t.mySender(),
								Membership:  event.MembershipJoin,
								PowerLevel:  &modLevel,
							},
						},
						PowerLevels: &bridgev2.PowerLevelOverrides{
							EventsDefault: &modLevel,
						},
					},
					CanBackfill: true,
				},
			})
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
	}

	peer, ok := msg.GetFromID()
	if !ok {
		peer = msg.GetPeerID()
	}
	switch from := peer.(type) {
	case *tg.PeerUser:
		return bridgev2.EventSender{
			SenderLogin: ids.MakeUserLoginID(from.UserID),
			Sender:      ids.MakeUserID(from.UserID),
		}
	case *tg.PeerChannel:
		return bridgev2.EventSender{
			Sender: ids.MakeChannelUserID(from.ChannelID),
		}
	default:
		fromID, _ := msg.GetFromID()
		panic(fmt.Sprintf("couldn't determine sender (from: %+v) (peer: %+v)", fromID, msg.GetPeerID()))
	}
}

func (t *TelegramClient) maybeUpdateRemoteProfile(ctx context.Context, ghost *bridgev2.Ghost, user *tg.User) error {
	if ghost.ID != t.userID {
		return nil
	}

	var changed bool
	if user != nil {
		fullName := util.FormatFullName(user.FirstName, user.LastName, user.Deleted, user.ID)
		username := user.Username
		if username == "" && len(user.Usernames) > 0 {
			username = user.Usernames[0].Username
		}

		normalizedPhone := "+" + strings.TrimPrefix(user.Phone, "+")
		remoteName := username
		if remoteName == "" {
			remoteName = normalizedPhone
		}
		if remoteName == "" {
			remoteName = fullName
		}

		changed = t.userLogin.RemoteName != remoteName ||
			t.userLogin.RemoteProfile.Phone != normalizedPhone ||
			t.userLogin.RemoteProfile.Username != username ||
			t.userLogin.RemoteProfile.Name != fullName
		t.userLogin.RemoteName = remoteName
		t.userLogin.RemoteProfile.Phone = normalizedPhone
		t.userLogin.RemoteProfile.Username = username
		t.userLogin.RemoteProfile.Name = fullName
	} else {
		changed = t.userLogin.RemoteName != ghost.Name
		t.userLogin.RemoteProfile.Name = ghost.Name
	}

	changed = changed || t.userLogin.RemoteProfile.Avatar != ghost.AvatarMXC
	t.userLogin.RemoteProfile.Avatar = ghost.AvatarMXC
	if changed {
		if err := t.userLogin.Save(ctx); err != nil {
			return err
		}
		t.userLogin.BridgeState.Send(status.BridgeState{StateEvent: status.StateConnected})
	}
	return nil
}

func (t *TelegramClient) onUserName(ctx context.Context, e tg.Entities, update *tg.UpdateUserName) error {
	ghost, err := t.main.Bridge.GetGhostByID(ctx, ids.MakeUserID(update.UserID))
	if err != nil {
		return err
	}

	name := util.FormatFullName(update.FirstName, update.LastName, false, update.UserID)
	userInfo := bridgev2.UserInfo{Name: &name}

	if len(update.Usernames) > 0 {
		for _, ident := range ghost.Identifiers {
			if !strings.HasPrefix(ident, "telegram:") {
				userInfo.Identifiers = append(userInfo.Identifiers, ident)
			}
		}

		for _, username := range update.Usernames {
			userInfo.Identifiers = append(userInfo.Identifiers, fmt.Sprintf("telegram:%s", username.Username))
		}

		slices.Sort(userInfo.Identifiers)
		userInfo.Identifiers = slices.Compact(userInfo.Identifiers)
	}

	ghost.UpdateInfo(ctx, &userInfo)
	return t.maybeUpdateRemoteProfile(ctx, ghost, nil)
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
			portalKey = t.makePortalKeyFromPeer(&tg.PeerChannel{ChannelID: channelID})
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

func (t *TelegramClient) updateGhost(ctx context.Context, userID int64, user *tg.User) (*bridgev2.UserInfo, error) {
	ghost, err := t.main.Bridge.GetGhostByID(ctx, ids.MakeUserID(userID))
	if err != nil {
		return nil, err
	}
	userInfo, err := t.getUserInfoFromTelegramUser(ctx, user)
	if err != nil {
		return nil, err
	}
	ghost.UpdateInfo(ctx, userInfo)
	return userInfo, t.maybeUpdateRemoteProfile(ctx, ghost, user)
}

func (t *TelegramClient) updateChannel(ctx context.Context, channel *tg.Channel) (*bridgev2.UserInfo, error) {
	if accessHash, ok := channel.GetAccessHash(); ok {
		if err := t.ScopedStore.SetAccessHash(ctx, ids.PeerTypeChannel, channel.ID, accessHash); err != nil {
			return nil, err
		}
	}

	if !channel.Broadcast {
		return nil, nil
	}

	// Update the channel ghost if this is a broadcast channel.
	ghost, err := t.main.Bridge.GetGhostByID(ctx, ids.MakeChannelUserID(channel.ID))
	if err != nil {
		return nil, err
	}

	var avatar *bridgev2.Avatar
	if photo, ok := channel.GetPhoto().(*tg.ChatPhoto); ok {
		avatar = &bridgev2.Avatar{
			ID: ids.MakeAvatarID(photo.PhotoID),
			Get: func(ctx context.Context) (data []byte, err error) {
				data, _, err = media.NewTransferer(t.client.API()).WithChannelPhoto(channel.ID, channel.AccessHash, photo.PhotoID).Download(ctx)
				return
			},
		}
	}

	if username, set := channel.GetUsername(); set {
		err := t.ScopedStore.SetUsername(ctx, ids.PeerTypeChannel, channel.ID, username)
		if err != nil {
			return nil, err
		}
	}

	userInfo := &bridgev2.UserInfo{
		Name:   &channel.Title,
		Avatar: avatar,
		ExtraUpdates: func(ctx context.Context, g *bridgev2.Ghost) bool {
			updated := !g.Metadata.(*GhostMetadata).IsChannel
			g.Metadata.(*GhostMetadata).IsChannel = true
			return updated
		},
	}
	ghost.UpdateInfo(ctx, userInfo)
	return userInfo, nil
}

func (t *TelegramClient) onEntityUpdate(ctx context.Context, e tg.Entities) error {
	for userID, user := range e.Users {
		if _, err := t.updateGhost(ctx, userID, user); err != nil {
			return err
		}
	}
	for _, channel := range e.Channels {
		if _, err := t.updateChannel(ctx, channel); err != nil {
			return err
		}
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

	// Check if this edit was a data export request acceptance message
	if sender.Sender == networkid.UserID("777000") {
		if strings.Contains(msg.Message, "Data export request") && strings.Contains(msg.Message, "Accepted") {
			zerolog.Ctx(ctx).Info().
				Int("message_id", msg.ID).
				Msg("Received an edit to message that looks like the data export was accepted, marking takeout as retriable")
			t.takeoutAccepted.Set()
		}
	}

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
			PortalKey: t.makePortalKeyFromPeer(msg.PeerID),
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
			PortalKey: t.makePortalKeyFromPeer(update.Peer),
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
		if accessHash, err := t.ScopedStore.GetAccessHash(ctx, ids.PeerTypeUser, id); err != nil {
			return nil, fmt.Errorf("failed to get user access hash for %d: %w", id, err)
		} else {
			return &tg.InputPeerUser{UserID: id, AccessHash: accessHash}, nil
		}
	case ids.PeerTypeChat:
		return &tg.InputPeerChat{ChatID: id}, nil
	case ids.PeerTypeChannel:
		if accessHash, err := t.ScopedStore.GetAccessHash(ctx, ids.PeerTypeChannel, id); err != nil {
			return nil, err
		} else {
			return &tg.InputPeerChannel{ChannelID: id, AccessHash: accessHash}, nil
		}
	default:
		panic("invalid peer type")
	}
}

func (t *TelegramClient) getAppConfigCached(ctx context.Context) (map[string]any, error) {
	t.appConfigLock.Lock()
	defer t.appConfigLock.Unlock()
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

func (t *TelegramClient) getAvailableReactions(ctx context.Context) (map[string]struct{}, error) {
	log := zerolog.Ctx(ctx).With().Str("handler", "get_available_reactions").Logger()
	t.availableReactionsLock.Lock()
	defer t.availableReactionsLock.Unlock()
	if t.availableReactions == nil || time.Since(t.availableReactionsFetched) > 12*time.Hour {
		cfg, err := t.client.API().MessagesGetAvailableReactions(ctx, t.availableReactionsHash)
		if err != nil {
			return nil, err
		}
		t.availableReactionsFetched = time.Now()
		switch v := cfg.(type) {
		case *tg.MessagesAvailableReactions:
			availableReactions, ok := cfg.(*tg.MessagesAvailableReactions)
			if !ok {
				return nil, fmt.Errorf("failed to get app config: unexpected type %T", availableReactions)
			}

			log.Debug().Msg("Fetched new available reactions")

			myGhost, err := t.main.Bridge.GetGhostByID(ctx, t.userID)
			if err != nil {
				log.Err(err).Msg("failed to get own ghost")
			}
			t.availableReactions = make(map[string]struct{}, len(availableReactions.Reactions))
			for _, reaction := range availableReactions.Reactions {
				if !reaction.Inactive && (myGhost.Metadata.(*GhostMetadata).IsPremium || !reaction.Premium) {
					t.availableReactions[reaction.Reaction] = struct{}{}
				}
			}

			t.availableReactionsHash = availableReactions.Hash
		case *tg.MessagesAvailableReactionsNotModified:
			log.Debug().Msg("Available reactions not modified")
		default:
			log.Error().Type("reaction_type", v).Msg("failed to get available reactions: unexpected type")
		}
	}
	return t.availableReactions, nil
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

func (t *TelegramClient) onNotifySettings(ctx context.Context, update *tg.UpdateNotifySettings) error {
	if update.Peer.TypeID() != tg.NotifyPeerTypeID {
		return fmt.Errorf("unsupported peer type %s", update.Peer.TypeName())
	}

	var mutedUntil *time.Time
	if mu, ok := update.NotifySettings.GetMuteUntil(); ok {
		mutedUntil = ptr.Ptr(time.Unix(int64(mu), 0))
	} else {
		mutedUntil = &bridgev2.Unmuted
	}

	t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatResync{
		ChatInfo: &bridgev2.ChatInfo{
			UserLocal: &bridgev2.UserLocalPortalInfo{
				MutedUntil: mutedUntil,
			},
			CanBackfill: true,
		},
		EventMeta: simplevent.EventMeta{
			Type:      bridgev2.RemoteEventChatResync,
			PortalKey: t.makePortalKeyFromPeer(update.Peer.(*tg.NotifyPeer).Peer),
		},
	})
	return nil
}

func (t *TelegramClient) HandleMute(ctx context.Context, msg *bridgev2.MatrixMute) error {
	inputPeer, err := t.inputPeerForPortalID(ctx, msg.Portal.ID)
	if err != nil {
		return err
	}
	_, err = t.client.API().AccountUpdateNotifySettings(ctx, &tg.AccountUpdateNotifySettingsRequest{
		Peer: &tg.InputNotifyPeer{Peer: inputPeer},
		Settings: tg.InputPeerNotifySettings{
			MuteUntil: int(msg.Content.GetMutedUntilTime().Unix()),
		},
	})
	return err
}

func (t *TelegramClient) onPinnedDialogs(ctx context.Context, msg *tg.UpdatePinnedDialogs) error {
	needsUnpinning := map[networkid.PortalKey]struct{}{}
	for _, portalID := range t.userLogin.Metadata.(*UserLoginMetadata).PinnedDialogs {
		pt, id, err := ids.ParsePortalID(portalID)
		if err != nil {
			return err
		}
		needsUnpinning[t.makePortalKeyFromID(pt, id)] = struct{}{}
	}
	t.userLogin.Metadata.(*UserLoginMetadata).PinnedDialogs = nil

	for _, d := range msg.Order {
		dialog, ok := d.(*tg.DialogPeer)
		if !ok {
			continue
		}
		portalKey := t.makePortalKeyFromPeer(dialog.Peer)
		delete(needsUnpinning, portalKey)
		t.userLogin.Metadata.(*UserLoginMetadata).PinnedDialogs = append(t.userLogin.Metadata.(*UserLoginMetadata).PinnedDialogs, portalKey.ID)

		t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatResync{
			ChatInfo: &bridgev2.ChatInfo{
				UserLocal: &bridgev2.UserLocalPortalInfo{
					Tag: ptr.Ptr(event.RoomTagFavourite),
				},
				CanBackfill: true,
			},
			EventMeta: simplevent.EventMeta{
				Type:      bridgev2.RemoteEventChatResync,
				PortalKey: portalKey,
			},
		})
	}

	var empty event.RoomTag
	for portalKey := range needsUnpinning {
		t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatResync{
			ChatInfo: &bridgev2.ChatInfo{
				UserLocal: &bridgev2.UserLocalPortalInfo{
					Tag: &empty,
				},
				CanBackfill: true,
			},
			EventMeta: simplevent.EventMeta{
				Type:      bridgev2.RemoteEventChatResync,
				PortalKey: portalKey,
			},
		})
	}

	return t.userLogin.Save(ctx)
}

func (t *TelegramClient) HandleRoomTag(ctx context.Context, msg *bridgev2.MatrixRoomTag) error {
	inputPeer, err := t.inputPeerForPortalID(ctx, msg.Portal.ID)
	if err != nil {
		return err
	}

	_, err = t.client.API().MessagesToggleDialogPin(ctx, &tg.MessagesToggleDialogPinRequest{
		Pinned: slices.Contains(maps.Keys(msg.Content.Tags), event.RoomTagFavourite),
		Peer:   &tg.InputDialogPeer{Peer: inputPeer},
	})
	return err
}

func (t *TelegramClient) onChatDefaultBannedRights(ctx context.Context, entities tg.Entities, update *tg.UpdateChatDefaultBannedRights) error {
	t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatResync{
		ChatInfo: &bridgev2.ChatInfo{
			Members: &bridgev2.ChatMemberList{
				PowerLevels: t.getPowerLevelOverridesFromBannedRights(entities.Chats[0], update.DefaultBannedRights),
			},
			CanBackfill: true,
		},
		EventMeta: simplevent.EventMeta{
			Type:      bridgev2.RemoteEventChatResync,
			PortalKey: t.makePortalKeyFromPeer(update.Peer),
		},
	})
	return nil
}

func (t *TelegramClient) onPeerBlocked(ctx context.Context, update *tg.UpdatePeerBlocked) error {
	var userID networkid.UserID
	if peer, ok := update.PeerID.(*tg.PeerUser); ok {
		userID = ids.MakeUserID(peer.UserID)
	} else {
		return fmt.Errorf("unexpected peer type in peer blocked update %T", update.PeerID)
	}

	// Update the ghost
	ghost, err := t.main.Bridge.GetGhostByID(ctx, userID)
	if err != nil {
		return err
	}
	ghost.UpdateInfo(ctx, &bridgev2.UserInfo{
		ExtraUpdates: func(ctx context.Context, g *bridgev2.Ghost) bool {
			updated := g.Metadata.(*GhostMetadata).Blocked != update.Blocked
			g.Metadata.(*GhostMetadata).Blocked = update.Blocked
			return updated
		},
	})

	// Find portals that are DMs with the user
	t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatResync{
		ChatInfo: &bridgev2.ChatInfo{
			Members: &bridgev2.ChatMemberList{
				PowerLevels: t.getDMPowerLevels(ghost),
			},
			CanBackfill: true,
		},
		EventMeta: simplevent.EventMeta{
			Type:      bridgev2.RemoteEventChatResync,
			PortalKey: t.makePortalKeyFromPeer(update.PeerID),
		},
	})
	return nil
}

func (t *TelegramClient) onChat(ctx context.Context, e tg.Entities, update *tg.UpdateChat) error {
	if _, ok := e.ChatsForbidden[update.ChatID]; ok {
		// The chat is now forbidden, we should leave it.
		t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatDelete{
			OnlyForMe: true,
			EventMeta: simplevent.EventMeta{
				Type:      bridgev2.RemoteEventChatDelete,
				PortalKey: t.makePortalKeyFromID(ids.PeerTypeChat, update.ChatID),
				Sender:    t.mySender(),
			},
		})
	}
	return nil
}
