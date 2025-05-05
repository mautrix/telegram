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
	"bytes"
	"context"
	"errors"
	"fmt"
	"slices"
	"strings"
	"time"

	"github.com/gotd/td/tg"
	"github.com/gotd/td/tgerr"
	"github.com/rs/zerolog"
	"go.mau.fi/util/exfmt"
	"go.mau.fi/util/ptr"
	"golang.org/x/exp/maps"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/bridgev2/simplevent"
	"maunium.net/go/mautrix/bridgev2/status"
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

func (t *TelegramClient) selfLeaveChat(portalKey networkid.PortalKey) {
	t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatDelete{
		EventMeta: simplevent.EventMeta{
			Type: bridgev2.RemoteEventChatDelete,
			LogContext: func(c zerolog.Context) zerolog.Context {
				return c.Stringer("portal_key", portalKey)
			},
			PortalKey: portalKey,
			Sender:    t.mySender(),
		},
		OnlyForMe: true,
	})
}

func (t *TelegramClient) onUpdateChannel(ctx context.Context, e tg.Entities, update *tg.UpdateChannel) error {
	log := zerolog.Ctx(ctx).With().
		Str("handler", "on_update_channel").
		Int64("channel_id", update.ChannelID).
		Logger()
	log.Debug().Msg("Fetching channel due to UpdateChannel event")

	portalKey := t.makePortalKeyFromID(ids.PeerTypeChannel, update.ChannelID)

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
			t.selfLeaveChat(portalKey)
			return nil
		}
		return fmt.Errorf("failed to get channel: %w", err)
	} else if len(chats.GetChats()) != 1 {
		return fmt.Errorf("expected 1 chat, got %d", len(chats.GetChats()))
	} else if channel, ok := chats.GetChats()[0].(*tg.Channel); !ok {
		log.Error().Type("chat_type", chats.GetChats()[0]).Msg("Expected channel, got something else. Leaving the channel.")
		t.selfLeaveChat(portalKey)
	} else if channel.Left {
		log.Error().Msg("Update was for a left channel. Leaving the channel.")
		t.selfLeaveChat(portalKey)
	} else {
		// TODO update the channel info
	}
	return nil
}

func (t *TelegramClient) onUpdateNewMessage(ctx context.Context, entities tg.Entities, update IGetMessage) error {
	log := zerolog.Ctx(ctx)
	switch msg := update.GetMessage().(type) {
	case *tg.Message:
		var isBroadcastChannel bool
		switch peer := msg.PeerID.(type) {
		case *tg.PeerChannel:
			log := log.With().Int64("channel_id", peer.ChannelID).Logger()
			if _, ok := entities.ChannelsForbidden[peer.ChannelID]; ok {
				log.Debug().Msg("Received message in forbidden channel, ignoring")
				return nil
			} else if c, ok := entities.Channels[peer.ChannelID]; ok && c.Left {
				log.Debug().Msg("Received message in left channel, ignoring")
				return nil
			} else if ok && !c.GetMegagroup() {
				isBroadcastChannel = true
			}
		case *tg.PeerChat:
			log := log.With().Int64("chat_id", peer.ChatID).Logger()
			if _, ok := entities.ChatsForbidden[peer.ChatID]; ok {
				log.Debug().Msg("Received message in forbidden chat, ignoring")
				return nil
			} else if c, ok := entities.Chats[peer.ChatID]; ok && c.Left {
				log.Debug().Msg("Received message in left chat, ignoring")
				return nil
			}
		}

		sender := t.getEventSender(msg, isBroadcastChannel)

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
				StreamOrder:  int64(msg.GetID()),
			},
			ID:                 ids.GetMessageIDFromMessage(msg),
			Data:               msg,
			ConvertMessageFunc: t.convertToMatrixWithRefetch,
		})

		t.handleTelegramReactions(ctx, msg)
	case *tg.MessageService:
		sender := t.getEventSender(msg, false)

		eventMeta := simplevent.EventMeta{
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
			StreamOrder: int64(msg.GetID()),
		}
		switch action := msg.Action.(type) {
		case *tg.MessageActionChatEditTitle:
			t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatInfoChange{
				EventMeta:      eventMeta.WithType(bridgev2.RemoteEventChatInfoChange),
				ChatInfoChange: &bridgev2.ChatInfoChange{ChatInfo: &bridgev2.ChatInfo{Name: &action.Title}},
			})
		case *tg.MessageActionChatEditPhoto:
			// FIXME
			chatID := msg.PeerID.(*tg.PeerChat).ChatID

			t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatInfoChange{
				EventMeta:      eventMeta.WithType(bridgev2.RemoteEventChatInfoChange),
				ChatInfoChange: &bridgev2.ChatInfoChange{ChatInfo: &bridgev2.ChatInfo{Avatar: t.avatarFromPhoto(ctx, ids.PeerTypeChat, chatID, action.Photo)}},
			})
		case *tg.MessageActionChatDeletePhoto:
			t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatInfoChange{
				EventMeta:      eventMeta.WithType(bridgev2.RemoteEventChatInfoChange),
				ChatInfoChange: &bridgev2.ChatInfoChange{ChatInfo: &bridgev2.ChatInfo{Avatar: &bridgev2.Avatar{Remove: true}}},
			})
		case *tg.MessageActionChatAddUser:
			memberChanges := &bridgev2.ChatMemberList{
				MemberMap: map[networkid.UserID]bridgev2.ChatMember{},
			}
			for _, userID := range action.Users {
				memberChanges.MemberMap[ids.MakeUserID(userID)] = bridgev2.ChatMember{
					EventSender: t.senderForUserID(userID),
					Membership:  event.MembershipJoin,
				}
			}
			t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatInfoChange{
				EventMeta:      eventMeta.WithType(bridgev2.RemoteEventChatInfoChange),
				ChatInfoChange: &bridgev2.ChatInfoChange{MemberChanges: memberChanges},
			})
		case *tg.MessageActionChatJoinedByLink:
			t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatInfoChange{
				EventMeta: eventMeta.WithType(bridgev2.RemoteEventChatInfoChange),
				ChatInfoChange: &bridgev2.ChatInfoChange{
					MemberChanges: &bridgev2.ChatMemberList{
						MemberMap: map[networkid.UserID]bridgev2.ChatMember{
							sender.Sender: {EventSender: sender, Membership: event.MembershipJoin},
						},
					},
				},
			})
		case *tg.MessageActionChatDeleteUser:
			if action.UserID == t.telegramUserID {
				t.selfLeaveChat(eventMeta.PortalKey)
				return nil
			}
			t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatInfoChange{
				EventMeta: eventMeta.WithType(bridgev2.RemoteEventChatInfoChange),
				ChatInfoChange: &bridgev2.ChatInfoChange{
					MemberChanges: &bridgev2.ChatMemberList{
						MemberMap: map[networkid.UserID]bridgev2.ChatMember{
							ids.MakeUserID(action.UserID): {
								EventSender: t.senderForUserID(action.UserID),
								Membership:  event.MembershipLeave,
							},
						},
					},
				},
			})
		case *tg.MessageActionChatCreate:
			memberMap := map[networkid.UserID]bridgev2.ChatMember{}
			for _, userID := range action.Users {
				memberMap[ids.MakeUserID(userID)] = bridgev2.ChatMember{
					EventSender: t.senderForUserID(userID),
					Membership:  event.MembershipJoin,
				}
			}

			t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatResync{
				EventMeta: eventMeta.
					WithType(bridgev2.RemoteEventChatResync).
					WithCreatePortal(true),
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
			t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.Message[any]{
				EventMeta: eventMeta.WithType(bridgev2.RemoteEventMessage),
				ID:        ids.GetMessageIDFromMessage(msg),
				ConvertMessageFunc: func(ctx context.Context, portal *bridgev2.Portal, intent bridgev2.MatrixAPI, data any) (*bridgev2.ConvertedMessage, error) {
					return &bridgev2.ConvertedMessage{
						Parts: []*bridgev2.ConvertedMessagePart{
							{
								Type:    event.EventMessage,
								Content: &event.MessageEventContent{MsgType: event.MsgNotice, Body: "Created the group"},
							},
						},
					}, nil
				},
			})

		case *tg.MessageActionChannelCreate:
			modLevel := 50
			t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatResync{
				EventMeta: eventMeta.
					WithType(bridgev2.RemoteEventChatResync).
					WithCreatePortal(true),
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
			t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.Message[any]{
				EventMeta: eventMeta.WithType(bridgev2.RemoteEventMessage),
				ID:        ids.GetMessageIDFromMessage(msg),
				ConvertMessageFunc: func(ctx context.Context, portal *bridgev2.Portal, intent bridgev2.MatrixAPI, data any) (*bridgev2.ConvertedMessage, error) {
					return &bridgev2.ConvertedMessage{
						Parts: []*bridgev2.ConvertedMessagePart{
							{
								Type:    event.EventMessage,
								Content: &event.MessageEventContent{MsgType: event.MsgNotice, Body: "Created the group"},
							},
						},
					}, nil
				},
			})
		case *tg.MessageActionSetMessagesTTL:
			t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatResync{
				EventMeta: eventMeta.WithType(bridgev2.RemoteEventChatResync),
				ChatInfo: &bridgev2.ChatInfo{
					ExtraUpdates: func(ctx context.Context, p *bridgev2.Portal) bool {
						updated := p.Portal.Metadata.(*PortalMetadata).MessagesTTL != action.Period
						p.Portal.Metadata.(*PortalMetadata).MessagesTTL = action.Period
						return updated
					},
				},
			})

			// Send a notice about the TTL change
			content := bridgev2.DisappearingMessageNotice(time.Duration(action.Period)*time.Second, false)
			t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.Message[any]{
				EventMeta: eventMeta.WithType(bridgev2.RemoteEventMessage),
				ID:        ids.GetMessageIDFromMessage(msg),
				ConvertMessageFunc: func(ctx context.Context, portal *bridgev2.Portal, intent bridgev2.MatrixAPI, data any) (*bridgev2.ConvertedMessage, error) {
					return &bridgev2.ConvertedMessage{
						Parts: []*bridgev2.ConvertedMessagePart{
							{Type: event.EventMessage, Content: content},
						},
					}, nil
				},
			})
		case *tg.MessageActionPhoneCall:
			var body strings.Builder
			if action.Video {
				body.WriteString("Video call ")
			} else {
				body.WriteString("Call ")
			}
			switch action.Reason.TypeID() {
			case tg.PhoneCallDiscardReasonMissedTypeID:
				body.WriteString("missed")
			case tg.PhoneCallDiscardReasonDisconnectTypeID:
				body.WriteString("disconnected")
			case tg.PhoneCallDiscardReasonHangupTypeID:
				body.WriteString("ended")
			case tg.PhoneCallDiscardReasonBusyTypeID:
				body.WriteString("rejected")
			default:
				return fmt.Errorf("unknown call end reason %T", action.Reason)
			}

			if action.Duration > 0 {
				body.WriteString(" (")
				body.WriteString(exfmt.Duration(time.Duration(action.Duration) * time.Second))
				body.WriteString(")")
			}

			t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.Message[any]{
				EventMeta: eventMeta.WithType(bridgev2.RemoteEventMessage),
				ID:        ids.GetMessageIDFromMessage(msg),
				ConvertMessageFunc: func(ctx context.Context, portal *bridgev2.Portal, intent bridgev2.MatrixAPI, data any) (*bridgev2.ConvertedMessage, error) {
					return &bridgev2.ConvertedMessage{
						Parts: []*bridgev2.ConvertedMessagePart{
							{
								Type:    event.EventMessage,
								Content: &event.MessageEventContent{MsgType: event.MsgNotice, Body: body.String()},
							},
						},
					}, nil
				},
			})
		case *tg.MessageActionGroupCall:
			var body strings.Builder
			if action.Duration == 0 {
				body.WriteString("Started a video chat")
			} else {
				body.WriteString("Ended the video chat")
				body.WriteString(" (")
				body.WriteString(exfmt.Duration(time.Duration(action.Duration) * time.Second))
				body.WriteString(")")
			}

			t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.Message[any]{
				EventMeta: eventMeta.WithType(bridgev2.RemoteEventMessage),
				ID:        ids.GetMessageIDFromMessage(msg),
				ConvertMessageFunc: func(ctx context.Context, portal *bridgev2.Portal, intent bridgev2.MatrixAPI, data any) (*bridgev2.ConvertedMessage, error) {
					return &bridgev2.ConvertedMessage{
						Parts: []*bridgev2.ConvertedMessagePart{
							{
								Type:    event.EventMessage,
								Content: &event.MessageEventContent{MsgType: event.MsgNotice, Body: body.String()},
							},
						},
					}, nil
				},
			})
		case *tg.MessageActionInviteToGroupCall:
			var body, html strings.Builder
			var mentions event.Mentions
			body.WriteString("Invited ")
			html.WriteString("Invited ")
			for i, userID := range action.Users {
				if i > 0 {
					body.WriteString(", ")
				}

				if ghost, err := t.main.Bridge.GetGhostByID(ctx, ids.MakeUserID(userID)); err != nil {
					return err
				} else {
					var name string
					if username, err := t.ScopedStore.GetUsername(ctx, ids.PeerTypeUser, userID); err != nil {
						name = "@" + username
					} else {
						name = ghost.Name
					}

					mentions.UserIDs = append(mentions.UserIDs, ghost.Intent.GetMXID())
					body.WriteString(name)
					html.WriteString(fmt.Sprintf(`<a href="%s">@%s</a>`, ghost.Intent.GetMXID().URI().MatrixToURL(), name))
				}
			}
			body.WriteString(" to the video chat")
			html.WriteString(" to the video chat")
			t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.Message[any]{
				EventMeta: eventMeta.WithType(bridgev2.RemoteEventMessage),
				ID:        ids.GetMessageIDFromMessage(msg),
				ConvertMessageFunc: func(ctx context.Context, portal *bridgev2.Portal, intent bridgev2.MatrixAPI, data any) (*bridgev2.ConvertedMessage, error) {
					return &bridgev2.ConvertedMessage{
						Parts: []*bridgev2.ConvertedMessagePart{
							{
								Type: event.EventMessage,
								Content: &event.MessageEventContent{
									MsgType:       event.MsgNotice,
									Body:          body.String(),
									Format:        event.FormatHTML,
									FormattedBody: html.String(),
									Mentions:      &mentions,
								},
							},
						},
					}, nil
				},
			})
		case *tg.MessageActionGroupCallScheduled:
			start := time.Unix(int64(action.ScheduleDate), 0)
			t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.Message[any]{
				EventMeta: eventMeta.
					WithType(bridgev2.RemoteEventMessage).
					WithSender(bridgev2.EventSender{}), // Telegram shows it as not coming from a specific user
				ID: ids.GetMessageIDFromMessage(msg),
				ConvertMessageFunc: func(ctx context.Context, portal *bridgev2.Portal, intent bridgev2.MatrixAPI, data any) (*bridgev2.ConvertedMessage, error) {
					return &bridgev2.ConvertedMessage{
						Parts: []*bridgev2.ConvertedMessagePart{
							{
								Type: event.EventMessage,
								Content: &event.MessageEventContent{
									MsgType: event.MsgNotice,
									Body:    fmt.Sprintf("Video chat scheduled for %s", start.Format("Jan 2, 15:04")),
								},
							},
						},
					}, nil
				},
			})

		// case *tg.MessageActionChatMigrateTo:
		// case *tg.MessageActionChannelMigrateFrom:
		// case *tg.MessageActionPinMessage:
		// case *tg.MessageActionHistoryClear:
		// case *tg.MessageActionGameScore:
		// case *tg.MessageActionPaymentSentMe:
		// case *tg.MessageActionPaymentSent:
		// case *tg.MessageActionScreenshotTaken:
		// case *tg.MessageActionCustomAction:
		// case *tg.MessageActionBotAllowed:
		// case *tg.MessageActionSecureValuesSentMe:
		// case *tg.MessageActionSecureValuesSent:
		// case *tg.MessageActionContactSignUp:
		// case *tg.MessageActionGeoProximityReached:
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

	default:
		return fmt.Errorf("unknown message type %T", msg)
	}
	return nil
}

func (t *TelegramClient) getEventSender(msg interface {
	GetOut() bool
	GetFromID() (tg.PeerClass, bool)
	GetPeerID() tg.PeerClass
}, isBroadcastChannel bool) bridgev2.EventSender {
	if isBroadcastChannel && msg.GetPeerID().TypeID() == tg.PeerChannelTypeID {
		// Always send as the channel in broadcast channels. We set a
		// per-message profile to indicate the actual user it was from.
		return t.getPeerSender(msg.GetPeerID())
	}

	if msg.GetOut() {
		return t.mySender()
	}

	peer, ok := msg.GetFromID()
	if !ok {
		peer = msg.GetPeerID()
	}
	return t.getPeerSender(peer)
}

func (t *TelegramClient) getPeerSender(peer tg.PeerClass) bridgev2.EventSender {
	switch from := peer.(type) {
	case *tg.PeerUser:
		return t.senderForUserID(from.UserID)
	case *tg.PeerChannel:
		return bridgev2.EventSender{
			Sender: ids.MakeChannelUserID(from.ChannelID),
		}
	default:
		panic(fmt.Sprintf("couldn't determine sender (peer: %+v)", peer))
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
	if accessHash, ok := channel.GetAccessHash(); ok && !channel.Min {
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
		avatar, err = t.convertChatPhoto(ctx, channel.ID, channel.AccessHash, photo)
		if err != nil {
			return nil, err
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
	for chatID, chat := range e.Chats {
		if chat.GetLeft() {
			t.selfLeaveChat(t.makePortalKeyFromID(ids.PeerTypeChat, chatID))
		}
	}
	for _, channel := range e.Channels {
		if _, err := t.updateChannel(ctx, channel); err != nil {
			return err
		}
	}
	for channelID := range e.ChannelsForbidden {
		t.selfLeaveChat(t.makePortalKeyFromID(ids.PeerTypeChannel, channelID))
	}
	return nil
}

func (t *TelegramClient) onMessageEdit(ctx context.Context, update IGetMessage) error {
	msg, ok := update.GetMessage().(*tg.Message)
	if !ok {
		return fmt.Errorf("edit message is not *tg.Message")
	}

	t.handleTelegramReactions(ctx, msg)

	portalKey := t.makePortalKeyFromPeer(msg.PeerID)
	portal, err := t.main.Bridge.GetPortalByKey(ctx, portalKey)
	if err != nil {
		return err
	}
	sender := t.getEventSender(msg, !portal.Metadata.(*PortalMetadata).IsSuperGroup)

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
			PortalKey: portalKey,
			Timestamp: time.Unix(int64(msg.EditDate), 0),
		},
		ID:            ids.GetMessageIDFromMessage(msg),
		TargetMessage: ids.GetMessageIDFromMessage(msg),
		Data:          msg,
		ConvertEditFunc: func(ctx context.Context, portal *bridgev2.Portal, intent bridgev2.MatrixAPI, existing []*database.Message, data *tg.Message) (*bridgev2.ConvertedEdit, error) {
			log := zerolog.Ctx(ctx)
			converted, err := t.convertToMatrixWithRefetch(ctx, portal, intent, msg)
			if err != nil {
				return nil, err
			}

			existingPart := existing[0]
			if len(existing) > 1 {
				log.Warn().Msg("Multiple parts found, using the first one that has a nonzero timestamp")
				for _, e := range existing {
					if !e.Timestamp.IsZero() {
						existingPart = e
						break
					}
				}
			}

			var ce bridgev2.ConvertedEdit
			if !bytes.Equal(existingPart.Metadata.(*MessageMetadata).ContentHash, converted.Parts[0].DBMetadata.(*MessageMetadata).ContentHash) {
				ce.ModifiedParts = append(ce.ModifiedParts, converted.Parts[0].ToEditPart(existingPart))
			}
			if len(ce.ModifiedParts) == 0 {
				return nil, bridgev2.ErrIgnoringRemoteEvent
			}
			return &ce, nil
		},
	})

	return nil
}

func (t *TelegramClient) handleTyping(portal networkid.PortalKey, sender bridgev2.EventSender, action tg.SendMessageActionClass) error {
	if sender.IsFromMe || (sender.Sender == t.userID && sender.SenderLogin == t.userLogin.ID) {
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
			Sender:    sender,
		},
		Timeout: timeout,
	})
	return nil
}

func (t *TelegramClient) updateReadReceipt(ctx context.Context, e tg.Entities, update *tg.UpdateReadHistoryOutbox) error {
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

func (t *TelegramClient) getAvailableReactionsForCapability(ctx context.Context) ([]string, bool) {
	_, err := t.getAvailableReactions(ctx)
	if err != nil {
		zerolog.Ctx(ctx).Warn().Err(err).Msg("Failed to get available reactions for capability listing")
	}
	return t.availableReactionsList, t.isPremiumCache.Load()
}

func (t *TelegramClient) getAvailableReactions(ctx context.Context) (map[string]struct{}, error) {
	if !t.IsLoggedIn() {
		return nil, errors.New("you must be logged in to get available reactions")
	}

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
			if myGhost.Metadata.(*GhostMetadata).IsPremium {
				// All reactions are allowed via the unicodemojipack feature
				t.availableReactionsList = nil
				t.isPremiumCache.Store(true)
			} else {
				t.availableReactionsList = maps.Keys(t.availableReactions)
				t.isPremiumCache.Store(false)
				slices.Sort(t.availableReactionsList)
			}
		case *tg.MessagesAvailableReactionsNotModified:
			log.Debug().Msg("Available reactions not modified")
		default:
			log.Error().Type("reaction_type", v).Msg("failed to get available reactions: unexpected type")
		}
	}
	return t.availableReactions, nil
}

func (t *TelegramClient) transferEmojisToMatrix(ctx context.Context, customEmojiIDs []int64) (result map[networkid.EmojiID]emojis.EmojiInfo, err error) {
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
		result[ids.MakeEmojiIDFromDocumentID(customEmojiDocument.GetID())] = emojis.EmojiInfo{EmojiURI: mxcURI}
	}
	return
}

func (t *TelegramClient) onNotifySettings(ctx context.Context, e tg.Entities, update *tg.UpdateNotifySettings) error {
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

func (t *TelegramClient) onPinnedDialogs(ctx context.Context, e tg.Entities, msg *tg.UpdatePinnedDialogs) error {
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

func (t *TelegramClient) onPeerBlocked(ctx context.Context, e tg.Entities, update *tg.UpdatePeerBlocked) error {
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
		t.selfLeaveChat(t.makePortalKeyFromID(ids.PeerTypeChat, update.ChatID))
	}
	return nil
}

func (t *TelegramClient) onPhoneCall(ctx context.Context, e tg.Entities, update *tg.UpdatePhoneCall) error {
	log := zerolog.Ctx(ctx).With().Str("action", "on_phone_call").Logger()
	call, ok := update.PhoneCall.(*tg.PhoneCallRequested)
	if !ok {
		log.Info().Type("type", update.PhoneCall).Msg("Unhandled phone call update class")
		return nil
	} else if call.ParticipantID != t.telegramUserID {
		return fmt.Errorf("received phone call for user that is not us")
	}

	var body strings.Builder
	body.WriteString("Started a ")
	if call.Video {
		body.WriteString("video call")
	} else {
		body.WriteString("call")
	}
	t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.Message[any]{
		EventMeta: simplevent.EventMeta{
			Type:         bridgev2.RemoteEventMessage,
			PortalKey:    t.makePortalKeyFromID(ids.PeerTypeUser, call.AdminID),
			CreatePortal: true,
			Sender:       t.senderForUserID(call.AdminID),
		},
		ID: networkid.MessageID(fmt.Sprintf("requested-%d", call.ID)),
		ConvertMessageFunc: func(ctx context.Context, portal *bridgev2.Portal, intent bridgev2.MatrixAPI, data any) (*bridgev2.ConvertedMessage, error) {
			return &bridgev2.ConvertedMessage{
				Parts: []*bridgev2.ConvertedMessagePart{
					{
						Type:    event.EventMessage,
						Content: &event.MessageEventContent{MsgType: event.MsgNotice, Body: body.String()},
					},
				},
			}, nil
		},
	})
	return nil
}
