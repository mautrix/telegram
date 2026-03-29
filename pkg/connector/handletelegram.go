// mautrix - A Matrix-Telegram puppeting bridge.
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
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tgerr"
)

type IGetMessage interface {
	GetMessage() tg.MessageClass
}

type IGetMessages interface {
	GetMessages() []int
}

func (t *TelegramClient) selfLeaveChat(ctx context.Context, portalKey networkid.PortalKey, reason error) error {
	peerType, id, _, err := ids.ParsePortalID(portalKey.ID)
	if err != nil {
		return err
	}
	if peerType == ids.PeerTypeChannel {
		t.updatesManager.RemoveChannel(id, reason)
		topics, err := t.main.Store.Topic.GetAll(ctx, id)
		if err != nil {
			return err
		}
		for _, topicID := range topics {
			res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatDelete{
				EventMeta: simplevent.EventMeta{
					Type:      bridgev2.RemoteEventChatDelete,
					PortalKey: t.makePortalKeyFromID(peerType, id, topicID),
					Sender:    t.mySender(),
					LogContext: func(c zerolog.Context) zerolog.Context {
						return c.AnErr("self_leave_reason", reason)
					},
				},
				OnlyForMe: true,
			})
			if err = resultToError(res); err != nil {
				return err
			}
		}
	}
	res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatDelete{
		EventMeta: simplevent.EventMeta{
			Type:      bridgev2.RemoteEventChatDelete,
			PortalKey: portalKey,
			Sender:    t.mySender(),
			LogContext: func(c zerolog.Context) zerolog.Context {
				return c.AnErr("self_leave_reason", reason)
			},
		},
		OnlyForMe: true,
	})
	if err = resultToError(res); err != nil {
		return err
	}
	if peerType == ids.PeerTypeChannel {
		// This is a no-op if there's no space portal
		res = t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatDelete{
			EventMeta: simplevent.EventMeta{
				Type:      bridgev2.RemoteEventChatDelete,
				PortalKey: t.makePortalKeyFromID(peerType, id, ids.TopicIDSpaceRoom),
				Sender:    t.mySender(),
				LogContext: func(c zerolog.Context) zerolog.Context {
					return c.AnErr("self_leave_reason", reason)
				},
			},
			OnlyForMe: true,
		})
		if err = resultToError(res); err != nil {
			return err
		}
	}
	return nil
}

func (t *TelegramClient) onNotChannelMember(ctx context.Context, channelID int64) error {
	return t.selfLeaveChat(ctx, t.makePortalKeyFromID(ids.PeerTypeChannel, channelID, 0), fmt.Errorf("startup channel member check failed"))
}

func (t *TelegramClient) onUpdateChannel(ctx context.Context, e tg.Entities, update *tg.UpdateChannel) error {
	log := zerolog.Ctx(ctx).With().
		Str("handler", "on_update_channel").
		Int64("channel_id", update.ChannelID).
		Logger()
	log.Debug().Msg("Fetching channel due to UpdateChannel event")

	// TODO resync topic portals?
	portalKey := t.makePortalKeyFromID(ids.PeerTypeChannel, update.ChannelID, 0)

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
			return t.selfLeaveChat(ctx, portalKey, fmt.Errorf("error fetching after UpdateChannel: %w", err))
		}
		log.Err(err).Msg("Failed to get channel info after UpdateChannel event")
	} else if len(chats.GetChats()) != 1 {
		log.Warn().Int("chat_count", len(chats.GetChats())).Msg("Got more than 1 chat in GetChannels response")
	} else if channel, ok := chats.GetChats()[0].(*tg.Channel); !ok {
		log.Error().Type("chat_type", chats.GetChats()[0]).Msg("Expected channel, got something else. Leaving the channel.")
		return t.selfLeaveChat(ctx, portalKey, fmt.Errorf("channel not returned in getChannels after UpdateChannel"))
	} else if channel.Left {
		log.Error().Msg("Update was for a left channel. Leaving the channel.")
		return t.selfLeaveChat(ctx, portalKey, fmt.Errorf("channel has left=true in getChannels after UpdateChannel"))
	} else {
		res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatResync{
			EventMeta: simplevent.EventMeta{
				Type:         bridgev2.RemoteEventChatResync,
				PortalKey:    portalKey,
				CreatePortal: true,
				LogContext: func(c zerolog.Context) zerolog.Context {
					return c.Str("tg_event", "updateChannel")
				},
			},
			GetChatInfoFunc: func(ctx context.Context, portal *bridgev2.Portal) (*bridgev2.ChatInfo, error) {
				chatInfo, mfm, err := t.wrapChatInfo(portal.ID, channel)
				if err != nil {
					return nil, err
				}
				if portal.MXID == "" {
					err = t.fillChannelMembers(ctx, mfm, chatInfo.Members)
					if err != nil {
						return nil, err
					}
				}
				return chatInfo, nil
			},
		})
		return resultToError(res)
	}
	return nil
}

func (t *TelegramClient) onUpdateNewMessage(ctx context.Context, entities tg.Entities, update IGetMessage) error {
	log := *zerolog.Ctx(ctx)
	switch msg := update.GetMessage().(type) {
	case *tg.Message:
		var isBroadcastChannel bool
		switch peer := msg.PeerID.(type) {
		case *tg.PeerChannel:
			log = log.With().Int64("channel_id", peer.ChannelID).Logger()
			if c, ok := entities.Channels[peer.ChannelID]; ok && c.Left {
				log.Debug().Msg("Received message in left channel, ignoring")
				return nil
			} else if ok && !c.GetMegagroup() {
				isBroadcastChannel = true
			}
		case *tg.PeerChat:
			log = log.With().Int64("chat_id", peer.ChatID).Logger()
			if c, ok := entities.Chats[peer.ChatID]; ok && c.Left {
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

		res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.Message[*tg.Message]{
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
				PortalKey:    t.makePortalKeyFromPeer(msg.PeerID, t.getTopicID(ctx, msg.PeerID, msg.ReplyTo)),
				CreatePortal: true,
				Timestamp:    time.Unix(int64(msg.Date), 0),
				StreamOrder:  int64(msg.GetID()),
			},
			ID:                 ids.GetMessageIDFromMessage(msg),
			Data:               msg,
			ConvertMessageFunc: t.convertToMatrix,
		})

		if err := resultToError(res); err != nil {
			return err
		}

		return t.handleTelegramReactions(ctx, msg)
	case *tg.MessageService:
		return t.handleServiceMessage(ctx, msg)

	default:
		log.Warn().
			Type("action_type", msg).
			Msg("ignoring unknown message type")
		return nil
	}
}

func (t *TelegramClient) getTopicID(ctx context.Context, peerID tg.PeerClass, rawReplyTo tg.MessageReplyHeaderClass) int {
	topicID := rawGetTopicID(rawReplyTo)
	if topicID != 0 {
		channelPeer, _ := peerID.(*tg.PeerChannel)
		err := t.main.Store.Topic.Add(ctx, channelPeer.GetChannelID(), topicID)
		if err != nil {
			zerolog.Ctx(ctx).Err(err).Msg("Failed to save topic ID")
		}
	}
	return topicID
}

func rawGetTopicID(rawReplyTo tg.MessageReplyHeaderClass) int {
	switch replyTo := rawReplyTo.(type) {
	case *tg.MessageReplyHeader:
		if replyTo.ForumTopic {
			if replyTo.ReplyToTopID != 0 {
				return replyTo.ReplyToTopID
			}
			return replyTo.ReplyToMsgID
		}
	}
	return 0
}

func (t *TelegramClient) handleServiceMessage(ctx context.Context, msg *tg.MessageService) error {
	log := zerolog.Ctx(ctx)
	sender := t.getEventSender(msg, false)

	eventMeta := simplevent.EventMeta{
		PortalKey: t.makePortalKeyFromPeer(msg.PeerID, t.getTopicID(ctx, msg.PeerID, msg.ReplyTo)),
		Sender:    sender,
		Timestamp: time.Unix(int64(msg.Date), 0),
		LogContext: func(c zerolog.Context) zerolog.Context {
			return c.
				Int("message_id", msg.GetID()).
				Str("sender", string(sender.Sender)).
				Str("sender_login", string(sender.SenderLogin)).
				Bool("is_from_me", sender.IsFromMe).
				Stringer("peer_id", msg.PeerID).
				Type("action_message_type", msg.Action)
		},
		StreamOrder: int64(msg.GetID()),
	}
	switch action := msg.Action.(type) {
	case *tg.MessageActionChatEditTitle:
		res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatInfoChange{
			EventMeta:      eventMeta.WithType(bridgev2.RemoteEventChatInfoChange),
			ChatInfoChange: &bridgev2.ChatInfoChange{ChatInfo: &bridgev2.ChatInfo{Name: &action.Title}},
		})
		return resultToError(res)
	case *tg.MessageActionChatEditPhoto:
		switch peer := msg.PeerID.(type) {
		case *tg.PeerChat:
			res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatInfoChange{
				EventMeta: eventMeta.WithType(bridgev2.RemoteEventChatInfoChange),
				ChatInfoChange: &bridgev2.ChatInfoChange{ChatInfo: &bridgev2.ChatInfo{
					Avatar: t.avatarFromPhoto(ctx, ids.PeerTypeChat, peer.ChatID, action.Photo),
				}},
			})
			return resultToError(res)
		case *tg.PeerChannel:
			res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatInfoChange{
				EventMeta: eventMeta.WithType(bridgev2.RemoteEventChatInfoChange),
				ChatInfoChange: &bridgev2.ChatInfoChange{ChatInfo: &bridgev2.ChatInfo{
					Avatar: t.avatarFromPhoto(ctx, ids.PeerTypeChannel, peer.ChannelID, action.Photo),
				}},
			})
			return resultToError(res)
		default:
			return nil
		}

	case *tg.MessageActionChatDeletePhoto:
		res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatInfoChange{
			EventMeta:      eventMeta.WithType(bridgev2.RemoteEventChatInfoChange),
			ChatInfoChange: &bridgev2.ChatInfoChange{ChatInfo: &bridgev2.ChatInfo{Avatar: &bridgev2.Avatar{Remove: true}}},
		})
		return resultToError(res)
	case *tg.MessageActionChatAddUser:
		memberChanges := &bridgev2.ChatMemberList{
			MemberMap: map[networkid.UserID]bridgev2.ChatMember{},
		}
		for _, userID := range action.Users {
			memberChanges.MemberMap.Set(bridgev2.ChatMember{
				EventSender: t.senderForUserID(userID),
				Membership:  event.MembershipJoin,
			})
		}
		res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatInfoChange{
			EventMeta:      eventMeta.WithType(bridgev2.RemoteEventChatInfoChange),
			ChatInfoChange: &bridgev2.ChatInfoChange{MemberChanges: memberChanges},
		})
		return resultToError(res)
	case *tg.MessageActionChatJoinedByLink:
		res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatInfoChange{
			EventMeta: eventMeta.WithType(bridgev2.RemoteEventChatInfoChange),
			ChatInfoChange: &bridgev2.ChatInfoChange{
				MemberChanges: &bridgev2.ChatMemberList{
					MemberMap: bridgev2.ChatMemberMap{}.Set(bridgev2.ChatMember{
						EventSender: sender,
						Membership:  event.MembershipJoin,
					}),
				},
			},
		})
		return resultToError(res)
	case *tg.MessageActionChatDeleteUser:
		if action.UserID == t.telegramUserID {
			return t.selfLeaveChat(ctx, eventMeta.PortalKey, fmt.Errorf("delete user event for chat"))
		}
		res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatInfoChange{
			EventMeta: eventMeta.WithType(bridgev2.RemoteEventChatInfoChange),
			ChatInfoChange: &bridgev2.ChatInfoChange{
				MemberChanges: &bridgev2.ChatMemberList{
					MemberMap: bridgev2.ChatMemberMap{}.Set(bridgev2.ChatMember{
						EventSender: t.senderForUserID(action.UserID),
						Membership:  event.MembershipLeave,
					}),
				},
			},
		})
		return resultToError(res)
	case *tg.MessageActionChatCreate:
		res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.Message[any]{
			EventMeta: eventMeta.WithType(bridgev2.RemoteEventMessage).WithCreatePortal(true),
			ID:        ids.GetMessageIDFromMessage(msg),
			ConvertMessageFunc: func(ctx context.Context, portal *bridgev2.Portal, intent bridgev2.MatrixAPI, data any) (*bridgev2.ConvertedMessage, error) {
				return &bridgev2.ConvertedMessage{
					Parts: []*bridgev2.ConvertedMessagePart{{
						Type: event.EventMessage,
						Content: &event.MessageEventContent{
							MsgType: event.MsgNotice,
							Body:    "Created the group",
						},
					}},
				}, nil
			},
		})
		return resultToError(res)

	case *tg.MessageActionChannelCreate:
		res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatResync{
			EventMeta: eventMeta.
				WithType(bridgev2.RemoteEventChatResync).
				WithCreatePortal(true),
			GetChatInfoFunc: t.GetChatInfo,
		})
		if err := resultToError(res); err != nil {
			return err
		}
		res = t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.Message[any]{
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
		return resultToError(res)
	case *tg.MessageActionSetMessagesTTL:
		setting := database.DisappearingSetting{
			Type:  event.DisappearingTypeAfterSend,
			Timer: time.Duration(action.Period) * time.Second,
		}.Normalize()
		res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatInfoChange{
			EventMeta: eventMeta.WithType(bridgev2.RemoteEventChatInfoChange),
			ChatInfoChange: &bridgev2.ChatInfoChange{
				ChatInfo: &bridgev2.ChatInfo{
					Disappear: &setting,
				},
			},
		})
		return resultToError(res)
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
			log.Warn().Stringer("end_reason", action.Reason).Msg("Unknown call end reason")
			return nil
		}

		if action.Duration > 0 {
			body.WriteString(" (")
			body.WriteString(exfmt.Duration(time.Duration(action.Duration) * time.Second))
			body.WriteString(")")
		}

		res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.Message[any]{
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
		return resultToError(res)
	case *tg.MessageActionGroupCall:
		var body string
		if action.Duration == 0 {
			body = "Started a video chat"
		} else {
			body = fmt.Sprintf("Ended the video chat (%s)", exfmt.Duration(time.Duration(action.Duration)*time.Second))
		}

		res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.Message[any]{
			EventMeta: eventMeta.WithType(bridgev2.RemoteEventMessage),
			ID:        ids.GetMessageIDFromMessage(msg),
			ConvertMessageFunc: func(ctx context.Context, portal *bridgev2.Portal, intent bridgev2.MatrixAPI, data any) (*bridgev2.ConvertedMessage, error) {
				return &bridgev2.ConvertedMessage{
					Parts: []*bridgev2.ConvertedMessagePart{{
						Type: event.EventMessage,
						Content: &event.MessageEventContent{
							MsgType: event.MsgNotice,
							Body:    body,
							BeeperActionMessage: &event.BeeperActionMessage{
								Type:     event.BeeperActionMessageCall,
								CallType: event.BeeperActionMessageCallTypeVideo,
							},
						},
					}},
				}, nil
			},
		})
		return resultToError(res)
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
				if username, err := t.main.Store.Username.Get(ctx, ids.PeerTypeUser, userID); err != nil {
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
		res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.Message[any]{
			EventMeta: eventMeta.WithType(bridgev2.RemoteEventMessage),
			ID:        ids.GetMessageIDFromMessage(msg),
			ConvertMessageFunc: func(ctx context.Context, portal *bridgev2.Portal, intent bridgev2.MatrixAPI, data any) (*bridgev2.ConvertedMessage, error) {
				return &bridgev2.ConvertedMessage{
					Parts: []*bridgev2.ConvertedMessagePart{{
						Type: event.EventMessage,
						Content: &event.MessageEventContent{
							MsgType:       event.MsgNotice,
							Body:          body.String(),
							Format:        event.FormatHTML,
							FormattedBody: html.String(),
							Mentions:      &mentions,
						},
					}},
				}, nil
			},
		})
		return resultToError(res)
	case *tg.MessageActionGroupCallScheduled:
		start := time.Unix(int64(action.ScheduleDate), 0)
		res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.Message[any]{
			EventMeta: eventMeta.
				WithType(bridgev2.RemoteEventMessage).
				WithSender(bridgev2.EventSender{}), // Telegram shows it as not coming from a specific user
			ID: ids.GetMessageIDFromMessage(msg),
			ConvertMessageFunc: func(ctx context.Context, portal *bridgev2.Portal, intent bridgev2.MatrixAPI, data any) (*bridgev2.ConvertedMessage, error) {
				return &bridgev2.ConvertedMessage{
					Parts: []*bridgev2.ConvertedMessagePart{{
						Type: event.EventMessage,
						Content: &event.MessageEventContent{
							MsgType: event.MsgNotice,
							Body:    fmt.Sprintf("Video chat scheduled for %s", start.Format("Jan 2, 15:04")),
						},
					}},
				}, nil
			},
		})
		return resultToError(res)

	case *tg.MessageActionChatMigrateTo:
		log.Debug().
			Str("old_portal_id", string(eventMeta.PortalKey.ID)).
			Int64("channel_id", action.ChannelID).
			Msg("MessageActionChatMigrateTo")
		newPortalKey := t.makePortalKeyFromID(ids.PeerTypeChannel, action.ChannelID, 0)
		if err := t.migrateChat(ctx, eventMeta.PortalKey, newPortalKey); err != nil {
			log.Err(err).Msg("Failed to migrate chat to channel")
			return err
		}
		res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.Message[any]{
			EventMeta: eventMeta.
				WithPortalKey(newPortalKey).
				WithStreamOrder(0).
				WithType(bridgev2.RemoteEventMessage),
			ID: ids.GetMessageIDFromMessage(msg),
			ConvertMessageFunc: func(ctx context.Context, portal *bridgev2.Portal, intent bridgev2.MatrixAPI, data any) (*bridgev2.ConvertedMessage, error) {
				return &bridgev2.ConvertedMessage{
					Parts: []*bridgev2.ConvertedMessagePart{{
						Type: event.EventMessage,
						Content: &event.MessageEventContent{
							MsgType: event.MsgNotice,
							Body:    "Upgraded this group to a supergroup",
						},
					}},
				}, nil
			},
		})
		return resultToError(res)

	case *tg.MessageActionTopicCreate:
		channelPeer, _ := msg.PeerID.(*tg.PeerChannel)
		err := t.main.Store.Topic.Add(ctx, channelPeer.GetChannelID(), msg.ID)
		if err != nil {
			return fmt.Errorf("failed to store new topic: %w", err)
		}
		res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatResync{
			EventMeta: eventMeta.
				WithPortalKey(t.makePortalKeyFromPeer(msg.PeerID, msg.ID)).
				WithType(bridgev2.RemoteEventChatResync).
				WithCreatePortal(true),
			GetChatInfoFunc: t.GetChatInfo,
		})
		return resultToError(res)
	case *tg.MessageActionTopicEdit:
		// TODO specific changes?
		res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatResync{
			EventMeta: eventMeta.
				WithPortalKey(t.makePortalKeyFromPeer(msg.PeerID, msg.ID)).
				WithType(bridgev2.RemoteEventChatResync).
				WithCreatePortal(true),
			GetChatInfoFunc: t.GetChatInfo,
		})
		return resultToError(res)

	default:
		log.Warn().
			Type("action_type", action).
			Msg("ignoring unknown action type")
		return nil
	}
}

func (t *TelegramClient) migrateChat(ctx context.Context, oldPortalKey, newPortalKey networkid.PortalKey) error {
	if t.main.Config.AlwaysTombstoneOnSupergroupMigration {
		newPortal, err := t.main.Bridge.GetPortalByKey(ctx, newPortalKey)
		if err != nil {
			return fmt.Errorf("failed to get new portal for chat migration: %w", err)
		}
		info, err := t.GetChatInfo(ctx, newPortal)
		if err != nil {
			return fmt.Errorf("failed to get chat info for new portal: %w", err)
		}
		err = newPortal.CreateMatrixRoom(ctx, t.userLogin, info)
		if err != nil {
			return fmt.Errorf("failed to create Matrix room for new portal: %w", err)
		}
	}

	result, portal, err := t.main.Bridge.ReIDPortal(ctx, oldPortalKey, newPortalKey)
	if err != nil {
		return fmt.Errorf("failed to re-ID portal: %w", err)
	} else if result == bridgev2.ReIDResultSourceReIDd || result == bridgev2.ReIDResultTargetDeletedAndSourceReIDd {
		// If the source portal is re-ID'd, we need to sync metadata and participants.
		// If the source is deleted, then it doesn't matter, any existing target will already be correct
		info, err := t.GetChatInfo(ctx, portal)
		if err != nil {
			zerolog.Ctx(ctx).Err(err).Msg("Failed to get chat info after re-ID")
			if tgerr.Is(err, tg.ErrChannelPrivate) {
				go func() {
					select {
					case <-time.After(5 * time.Second):
					case <-ctx.Done():
						return
					}
					zerolog.Ctx(ctx).Debug().Msg("Retrying GetChatInfo after re-ID")
					info, err := t.GetChatInfo(ctx, portal)
					if err != nil {
						zerolog.Ctx(ctx).Err(err).Msg("Failed to get chat info after re-ID retry")
					} else {
						portal.UpdateInfo(ctx, info, t.userLogin, nil, time.Time{})
					}
				}()
			}
		} else {
			portal.UpdateInfo(ctx, info, t.userLogin, nil, time.Time{})
		}
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

func (t *TelegramClient) onUserName(ctx context.Context, e tg.Entities, update *tg.UpdateUserName) error {
	ghost, err := t.main.Bridge.GetGhostByID(ctx, ids.MakeUserID(update.UserID))
	if err != nil {
		return err
	}
	meta := ghost.Metadata.(*GhostMetadata)

	var userInfo bridgev2.UserInfo

	name := util.FormatFullName(update.FirstName, update.LastName, false, update.UserID)
	userInfo.Name = &name
	if meta.ContactSource != 0 && meta.ContactSource != t.telegramUserID && !t.main.Config.ContactNames {
		// TODO fetch full info to accurately detect if the user is a contact or not
		userInfo.Name = nil
	}

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
	if ghost.ID == t.userID {
		var firstUsername string
		if len(update.Usernames) > 0 {
			firstUsername = update.Usernames[0].Username
		}
		t.updateRemoteProfile(ctx, &tg.User{
			Self:      true,
			ID:        update.UserID,
			FirstName: update.FirstName,
			LastName:  update.LastName,
			Username:  firstUsername,
			Usernames: update.Usernames,
		}, ghost)
	}

	return nil
}

func (t *TelegramClient) onDeleteMessages(ctx context.Context, channelID int64, update IGetMessages) error {
	for _, messageID := range update.GetMessages() {
		// TODO have mautrix-go do this part too?
		parts, err := t.main.Bridge.DB.Message.GetAllPartsByID(ctx, t.loginID, ids.MakeMessageID(channelID, messageID))
		if err != nil {
			return err
		}
		if len(parts) == 0 {
			zerolog.Ctx(ctx).Debug().
				Int("message_id", messageID).
				Int64("channel_id", channelID).
				Msg("ignoring delete of message we have no parts for")
			continue
		}
		// TODO can deletes happen across rooms?
		portalKey := parts[0].Room
		// TODO optimize non-topic portal deletion by using channel ID?
		//portalKey = t.makePortalKeyFromPeer(&tg.PeerChannel{ChannelID: channelID})
		res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.MessageRemove{
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
		if err := resultToError(res); err != nil {
			return err
		}
	}
	return nil
}

func (t *TelegramClient) updateGhost(ctx context.Context, userID int64, user *tg.User) (*bridgev2.UserInfo, error) {
	ghost, err := t.main.Bridge.GetGhostByID(ctx, ids.MakeUserID(userID))
	if err != nil {
		return nil, err
	}
	userInfo, err := t.wrapUserInfo(ctx, user, ghost)
	if err != nil {
		return nil, err
	}
	ghost.UpdateInfo(ctx, userInfo)

	if !user.Min && ghost.ID == t.userID && t.updateRemoteProfile(ctx, user, ghost) {
		t.userLogin.BridgeState.Send(status.BridgeState{StateEvent: status.StateConnected})
	}

	return userInfo, nil
}

func (t *TelegramClient) updateChannel(ctx context.Context, channel *tg.Channel) (*bridgev2.UserInfo, error) {
	if accessHash, ok := channel.GetAccessHash(); ok && !channel.Min {
		if err := t.ScopedStore.SetAccessHash(ctx, ids.PeerTypeChannel, channel.ID, accessHash); err != nil {
			return nil, err
		}
	}

	// TODO resync portal metadata?

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
		avatar, err = t.convertChatPhoto(channel.AsInputPeer(), photo)
		if err != nil {
			return nil, err
		}
	}

	if username, set := channel.GetUsername(); set {
		err := t.main.Store.Username.Set(ctx, ids.PeerTypeChannel, channel.ID, username)
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

const updateHandlerStuck status.BridgeStateErrorCode = "tg-update-handler-stuck"

func (t *TelegramClient) onUpdateWrapper(ctx context.Context, e tg.Entities, upd tg.UpdateClass) error {
	doneChan := make(chan error, 1)
	go func() {
		doneChan <- t.onUpdate(ctx, e, upd)
	}()
	ticker := time.NewTicker(10 * time.Second)
	defer ticker.Stop()
	startedAt := time.Now()
	bridgeStateUpdated := false
	for {
		select {
		case <-ticker.C:
			zerolog.Ctx(ctx).Warn().
				Time("started_at", startedAt).
				Msg("Telegram update handling is taking long")
			if time.Since(startedAt) > 3*time.Minute && !bridgeStateUpdated {
				bridgeStateUpdated = true
				t.userLogin.BridgeState.Send(status.BridgeState{
					StateEvent: status.StateUnknownError,
					Error:      updateHandlerStuck,
					Message:    "Processing messages from Telegram is stuck",
				})
			}
		case err := <-doneChan:
			if bridgeStateUpdated && t.userLogin.BridgeState.GetPrevUnsent().Error == updateHandlerStuck {
				t.userLogin.BridgeState.Send(status.BridgeState{
					StateEvent: status.StateConnected,
					Info: map[string]any{
						"update_reason": "finished processing slow update",
					},
				})
			}
			return err
		}
	}
}

func (t *TelegramClient) onUpdate(ctx context.Context, e tg.Entities, upd tg.UpdateClass) error {
	zerolog.Ctx(ctx).Trace().Stringer("update", upd).Msg("Raw update")
	for userID, user := range e.Users {
		zerolog.Ctx(ctx).Trace().Stringer("user", user).Msg("Raw user info in update")
		if _, err := t.updateGhost(ctx, userID, user); err != nil {
			return err
		}
	}
	for chatID, chat := range e.Chats {
		zerolog.Ctx(ctx).Trace().Stringer("chat", chat).Msg("Raw chat info in update")
		if chat.GetLeft() {
			// TODO don't ignore errors
			t.selfLeaveChat(ctx, t.makePortalKeyFromID(ids.PeerTypeChat, chatID, 0), fmt.Errorf("left flag in entity update"))
		}
	}
	for _, channel := range e.Channels {
		zerolog.Ctx(ctx).Trace().Stringer("channel", channel).Msg("Raw channel info in update")
		if channel.GetLeft() {
			t.selfLeaveChat(ctx, t.makePortalKeyFromID(ids.PeerTypeChannel, channel.ID, 0), fmt.Errorf("left flag in entity update"))
		}
		if _, err := t.updateChannel(ctx, channel); err != nil {
			return err
		}
	}
	switch update := upd.(type) {
	case *tg.UpdateNewMessage:
		return t.onUpdateNewMessage(ctx, e, update)
	case *tg.UpdateNewChannelMessage:
		return t.onUpdateNewMessage(ctx, e, update)
	case *tg.UpdateChannel:
		return t.onUpdateChannel(ctx, e, update)
	case *tg.UpdateUserName:
		return t.onUserName(ctx, e, update)
	case *tg.UpdateDeleteMessages:
		return t.onDeleteMessages(ctx, 0, update)
	case *tg.UpdateDeleteChannelMessages:
		return t.onDeleteMessages(ctx, update.ChannelID, update)
	case *tg.UpdateEditMessage:
		return t.onMessageEdit(ctx, update)
	case *tg.UpdateEditChannelMessage:
		return t.onMessageEdit(ctx, update)
	case *tg.UpdateUserTyping:
		return t.handleTyping(t.makePortalKeyFromID(ids.PeerTypeUser, update.UserID, 0), t.senderForUserID(update.UserID), update.Action)
	case *tg.UpdateChatUserTyping:
		if update.FromID.TypeID() != tg.PeerUserTypeID {
			zerolog.Ctx(ctx).Warn().Str("from_id_type", update.FromID.TypeName()).Msg("unsupported from_id type")
			return nil
		}
		return t.handleTyping(t.makePortalKeyFromID(ids.PeerTypeChat, update.ChatID, 0), t.getPeerSender(update.FromID), update.Action)
	case *tg.UpdateChannelUserTyping:
		return t.handleTyping(t.makePortalKeyFromID(ids.PeerTypeChannel, update.ChannelID, update.TopMsgID), t.getPeerSender(update.FromID), update.Action)
	case *tg.UpdateReadHistoryOutbox:
		return t.updateReadReceipt(ctx, e, update)
	case *tg.UpdateReadHistoryInbox:
		return t.onOwnReadReceipt(t.makePortalKeyFromPeer(update.Peer, update.TopMsgID), update.MaxID)
	case *tg.UpdateReadChannelInbox:
		return t.onOwnReadReceipt(t.makePortalKeyFromID(ids.PeerTypeChannel, update.ChannelID, 0), update.MaxID)
	case *tg.UpdateNotifySettings:
		return t.onNotifySettings(ctx, e, update)
	case *tg.UpdatePinnedDialogs:
		return t.onPinnedDialogs(ctx, e, update)
	case *tg.UpdateChatDefaultBannedRights:
		return t.onChatDefaultBannedRights(ctx, e, update)
	case *tg.UpdatePeerBlocked:
		return t.onPeerBlocked(ctx, e, update)
	case *tg.UpdateChat:
		return t.onChat(ctx, e, update)
	case *tg.UpdatePhoneCall:
		return t.onPhoneCall(ctx, e, update)
	case *tg.UpdateUserStatus:
		// ignored
		return nil
	default:
		zerolog.Ctx(ctx).Debug().Type("update_type", update).Msg("Unhandled update type")
		return nil
	}
}

func (t *TelegramClient) onMessageEdit(ctx context.Context, update IGetMessage) error {
	msg, ok := update.GetMessage().(*tg.Message)
	if !ok {
		zerolog.Ctx(ctx).Warn().
			Str("type_name", update.GetMessage().TypeName()).
			Msg("edit message is not *tg.Message")
		return nil
	}

	err := t.handleTelegramReactions(ctx, msg)
	if err != nil {
		zerolog.Ctx(ctx).Err(err).Msg("Failed to handle reactions on edited message")
	}

	portalKey := t.makePortalKeyFromPeer(msg.PeerID, t.getTopicID(ctx, msg.PeerID, msg.ReplyTo))
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

	res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.Message[*tg.Message]{
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
			converted, err := t.convertToMatrix(ctx, portal, intent, msg)
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

			if bytes.Equal(existingPart.Metadata.(*MessageMetadata).ContentHash, converted.Parts[0].DBMetadata.(*MessageMetadata).ContentHash) {
				return nil, fmt.Errorf("%w (content hash didn't change)", bridgev2.ErrIgnoringRemoteEvent)
			}
			return &bridgev2.ConvertedEdit{
				ModifiedParts: []*bridgev2.ConvertedEditPart{converted.Parts[0].ToEditPart(existingPart)},
			}, nil
		},
	})
	return resultToError(res)
}

func (t *TelegramClient) handleTyping(portal networkid.PortalKey, sender bridgev2.EventSender, action tg.SendMessageActionClass) error {
	if sender.IsFromMe || (sender.Sender == t.userID && sender.SenderLogin == t.userLogin.ID) {
		return nil
	}
	timeout := time.Duration(6) * time.Second
	var typingType bridgev2.TypingType
	switch action.(type) {
	case *tg.SendMessageTypingAction:
		typingType = bridgev2.TypingTypeText
	case *tg.SendMessageRecordAudioAction, *tg.SendMessageRecordRoundAction, *tg.SendMessageRecordVideoAction:
		typingType = bridgev2.TypingTypeRecordingMedia
	case *tg.SendMessageUploadAudioAction, *tg.SendMessageUploadDocumentAction, *tg.SendMessageUploadPhotoAction, *tg.SendMessageUploadRoundAction, *tg.SendMessageUploadVideoAction:
		typingType = bridgev2.TypingTypeUploadingMedia
	case *tg.SendMessageCancelAction:
		timeout = 0
	default:
		timeout = 0
	}
	res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.Typing{
		EventMeta: simplevent.EventMeta{
			Type:      bridgev2.RemoteEventTyping,
			PortalKey: portal,
			Sender:    sender,
			LogContext: func(c zerolog.Context) zerolog.Context {
				return c.Str("tg_event", "update*Typing")
			},
		},
		Timeout: timeout,
		Type:    typingType,
	})
	return resultToError(res)
}

func (t *TelegramClient) updateReadReceipt(ctx context.Context, e tg.Entities, update *tg.UpdateReadHistoryOutbox) error {
	user, ok := update.Peer.(*tg.PeerUser)
	if !ok {
		// Read receipts from other users are meaningless in chats/channels
		// (they only say "someone read the message" and not who)
		return nil
	}
	res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.Receipt{
		EventMeta: simplevent.EventMeta{
			Type:      bridgev2.RemoteEventReadReceipt,
			PortalKey: t.makePortalKeyFromPeer(update.Peer, 0),
			Sender: bridgev2.EventSender{
				SenderLogin: ids.MakeUserLoginID(user.UserID),
				Sender:      ids.MakeUserID(user.UserID),
			},
			LogContext: func(c zerolog.Context) zerolog.Context {
				return c.Str("tg_event", "updateReadHistoryOutbox")
			},
		},
		LastTarget:          ids.MakeMessageID(update.Peer, update.MaxID),
		ReadUpToStreamOrder: int64(update.MaxID),
	})
	return resultToError(res)
}

func (t *TelegramClient) onOwnReadReceipt(portalKey networkid.PortalKey, maxID int) error {
	res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.Receipt{
		EventMeta: simplevent.EventMeta{
			Type:      bridgev2.RemoteEventReadReceipt,
			PortalKey: portalKey,
			Sender:    t.mySender(),
			LogContext: func(c zerolog.Context) zerolog.Context {
				return c.Str("tg_event", "updateRead*Inbox")
			},
		},
		LastTarget:          ids.MakeMessageID(portalKey, maxID),
		ReadUpToStreamOrder: int64(maxID),
	})
	return resultToError(res)
}

func (t *TelegramClient) inputPeerForPortalID(ctx context.Context, portalID networkid.PortalID) (tg.InputPeerClass, int, error) {
	peerType, id, topicID, err := ids.ParsePortalID(portalID)
	if err != nil {
		return nil, 0, err
	}
	switch peerType {
	case ids.PeerTypeUser:
		if accessHash, err := t.ScopedStore.GetAccessHash(ctx, ids.PeerTypeUser, id); err != nil {
			return nil, 0, fmt.Errorf("failed to get user access hash for %d: %w", id, err)
		} else {
			return &tg.InputPeerUser{UserID: id, AccessHash: accessHash}, 0, nil
		}
	case ids.PeerTypeChat:
		return &tg.InputPeerChat{ChatID: id}, 0, nil
	case ids.PeerTypeChannel:
		if accessHash, err := t.ScopedStore.GetAccessHash(ctx, ids.PeerTypeChannel, id); err != nil {
			return nil, 0, err
		} else {
			return &tg.InputPeerChannel{ChannelID: id, AccessHash: accessHash}, topicID, nil
		}
	default:
		panic("invalid peer type")
	}
}

func (t *TelegramClient) getAppConfigCached(ctx context.Context) (map[string]any, error) {
	if t.metadata.IsBot {
		return nil, nil
	}
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
	if t.metadata.IsBot {
		return nil, nil
	} else if !t.IsLoggedIn() {
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

	if t.main.useDirectMedia {
		for _, emojiID := range customEmojiIDs {
			mediaID, err := ids.DirectMediaInfo{
				PeerType: ids.FakePeerTypeEmoji,
				UserID:   t.telegramUserID,
				ID:       emojiID,
			}.AsMediaID()
			if err != nil {
				return nil, err
			}
			if mxcURI, err := t.main.Bridge.Matrix.GenerateContentURI(ctx, mediaID); err != nil {
				return nil, err
			} else {
				result[ids.MakeEmojiIDFromDocumentID(emojiID)] = emojis.EmojiInfo{EmojiURI: mxcURI, DocumentID: emojiID}
			}
		}

		return result, nil
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
		result[ids.MakeEmojiIDFromDocumentID(customEmojiDocument.GetID())] = emojis.EmojiInfo{EmojiURI: mxcURI, DocumentID: customEmojiDocument.GetID()}
	}
	return
}

func (t *TelegramClient) onNotifySettings(ctx context.Context, e tg.Entities, update *tg.UpdateNotifySettings) error {
	var portalKey networkid.PortalKey
	switch typedPeer := update.Peer.(type) {
	case *tg.NotifyPeer:
		portalKey = t.makePortalKeyFromPeer(typedPeer.Peer, 0)
	case *tg.NotifyForumTopic:
		portalKey = t.makePortalKeyFromPeer(typedPeer.Peer, typedPeer.TopMsgID)
	default:
		zerolog.Ctx(ctx).Debug().
			Type("peer_type", update.Peer).
			Any("peer", update.Peer).
			Msg("Ignoring unsupported notify settings peer type")
		return nil
	}

	var mutedUntil *time.Time
	if mu, ok := update.NotifySettings.GetMuteUntil(); ok {
		mutedUntil = ptr.Ptr(time.Unix(int64(mu), 0))
	} else {
		mutedUntil = &bridgev2.Unmuted
	}

	res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatInfoChange{
		ChatInfoChange: &bridgev2.ChatInfoChange{
			ChatInfo: &bridgev2.ChatInfo{
				UserLocal: &bridgev2.UserLocalPortalInfo{
					MutedUntil: mutedUntil,
				},
			},
		},
		EventMeta: simplevent.EventMeta{
			Type:      bridgev2.RemoteEventChatInfoChange,
			PortalKey: portalKey,
			LogContext: func(c zerolog.Context) zerolog.Context {
				return c.
					Str("tg_event", "updateNotifySettings").
					Time("muted_until", *mutedUntil)
			},
		},
	})
	return resultToError(res)
}

func (t *TelegramClient) onPinnedDialogs(ctx context.Context, e tg.Entities, msg *tg.UpdatePinnedDialogs) error {
	needsUnpinning := map[networkid.PortalKey]struct{}{}
	for _, portalID := range t.metadata.PinnedDialogs {
		pt, id, _, err := ids.ParsePortalID(portalID)
		if err != nil {
			return err
		}
		needsUnpinning[t.makePortalKeyFromID(pt, id, 0)] = struct{}{}
	}
	t.metadata.PinnedDialogs = nil

	for _, d := range msg.Order {
		dialog, ok := d.(*tg.DialogPeer)
		if !ok {
			continue
		}
		portalKey := t.makePortalKeyFromPeer(dialog.Peer, 0)
		delete(needsUnpinning, portalKey)
		t.metadata.PinnedDialogs = append(t.metadata.PinnedDialogs, portalKey.ID)

		res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatInfoChange{
			ChatInfoChange: &bridgev2.ChatInfoChange{
				ChatInfo: &bridgev2.ChatInfo{
					UserLocal: &bridgev2.UserLocalPortalInfo{
						Tag: ptr.Ptr(event.RoomTagFavourite),
					},
				},
			},
			EventMeta: simplevent.EventMeta{
				Type:      bridgev2.RemoteEventChatInfoChange,
				PortalKey: portalKey,
				LogContext: func(c zerolog.Context) zerolog.Context {
					return c.
						Str("tg_event", "updatePinnedDialogs").
						Bool("pinned", true)
				},
			},
		})
		if err := resultToError(res); err != nil {
			return err
		}
	}

	var empty event.RoomTag
	for portalKey := range needsUnpinning {
		res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatInfoChange{
			ChatInfoChange: &bridgev2.ChatInfoChange{
				ChatInfo: &bridgev2.ChatInfo{
					UserLocal: &bridgev2.UserLocalPortalInfo{
						Tag: &empty,
					},
				},
			},
			EventMeta: simplevent.EventMeta{
				Type:      bridgev2.RemoteEventChatInfoChange,
				PortalKey: portalKey,
				LogContext: func(c zerolog.Context) zerolog.Context {
					return c.
						Str("tg_event", "updatePinnedDialogs").
						Bool("pinned", false)
				},
			},
		})
		if err := resultToError(res); err != nil {
			return err
		}
	}

	return t.userLogin.Save(ctx)
}

func (t *TelegramClient) onChatDefaultBannedRights(ctx context.Context, entities tg.Entities, update *tg.UpdateChatDefaultBannedRights) error {
	// TODO update all topic portals
	res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatInfoChange{
		ChatInfoChange: &bridgev2.ChatInfoChange{
			ChatInfo: &bridgev2.ChatInfo{
				Members: &bridgev2.ChatMemberList{
					PowerLevels: t.getPowerLevelOverridesFromBannedRights(entities.Chats[0], update.DefaultBannedRights),
				},
			},
		},
		EventMeta: simplevent.EventMeta{
			Type:      bridgev2.RemoteEventChatInfoChange,
			PortalKey: t.makePortalKeyFromPeer(update.Peer, 0),
			LogContext: func(c zerolog.Context) zerolog.Context {
				return c.Str("tg_event", "updateChatDefaultBannedRights")
			},
		},
	})
	return resultToError(res)
}

func (t *TelegramClient) onPeerBlocked(ctx context.Context, e tg.Entities, update *tg.UpdatePeerBlocked) error {
	// TODO fix this after adding storage for block status (getDMPowerLevels also needs updating)
	if true {
		return nil
	}
	var userID networkid.UserID
	if peer, ok := update.PeerID.(*tg.PeerUser); ok {
		userID = ids.MakeUserID(peer.UserID)
	} else {
		zerolog.Ctx(ctx).Warn().Type("peer_type", update.PeerID).Msg("Unexpected peer type in peer blocked update")
		return nil
	}

	// Update the ghost
	ghost, err := t.main.Bridge.GetGhostByID(ctx, userID)
	if err != nil {
		return err
	}

	// Find portals that are DMs with the user
	res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatResync{
		ChatInfo: &bridgev2.ChatInfo{
			Members: &bridgev2.ChatMemberList{
				PowerLevels: t.getDMPowerLevels(ghost),
			},
			CanBackfill: true,
		},
		EventMeta: simplevent.EventMeta{
			Type:      bridgev2.RemoteEventChatResync,
			PortalKey: t.makePortalKeyFromPeer(update.PeerID, 0),
			LogContext: func(c zerolog.Context) zerolog.Context {
				return c.Str("tg_event", "updatePeerBlocked")
			},
		},
	})
	return resultToError(res)
}

func (t *TelegramClient) onChat(ctx context.Context, e tg.Entities, update *tg.UpdateChat) error {
	return nil
}

func (t *TelegramClient) onPhoneCall(ctx context.Context, e tg.Entities, update *tg.UpdatePhoneCall) error {
	log := zerolog.Ctx(ctx).With().Str("action", "on_phone_call").Logger()
	call, ok := update.PhoneCall.(*tg.PhoneCallRequested)
	if !ok {
		log.Info().Type("type", update.PhoneCall).Msg("Unhandled phone call update class")
		return nil
	} else if call.ParticipantID != t.telegramUserID {
		log.Warn().Msg("Received phone call for user that is not us")
		return nil
	}

	var callType event.BeeperActionMessageCallType
	var body strings.Builder
	body.WriteString("Started a ")
	if call.Video {
		callType = event.BeeperActionMessageCallTypeVideo
		body.WriteString("video call")
	} else {
		callType = event.BeeperActionMessageCallTypeVoice
		body.WriteString("call")
	}
	res := t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.Message[any]{
		EventMeta: simplevent.EventMeta{
			Type:         bridgev2.RemoteEventMessage,
			PortalKey:    t.makePortalKeyFromID(ids.PeerTypeUser, call.AdminID, 0),
			CreatePortal: true,
			Sender:       t.senderForUserID(call.AdminID),
			LogContext: func(c zerolog.Context) zerolog.Context {
				return c.Str("tg_event", "updatePhoneCall")
			},
		},
		ID: networkid.MessageID(fmt.Sprintf("requested-%d", call.ID)),
		ConvertMessageFunc: func(ctx context.Context, portal *bridgev2.Portal, intent bridgev2.MatrixAPI, data any) (*bridgev2.ConvertedMessage, error) {
			return &bridgev2.ConvertedMessage{
				Parts: []*bridgev2.ConvertedMessagePart{
					{
						Type: event.EventMessage,
						Content: &event.MessageEventContent{
							MsgType: event.MsgNotice,
							Body:    body.String(),
							BeeperActionMessage: &event.BeeperActionMessage{
								Type:     event.BeeperActionMessageCall,
								CallType: callType,
							},
						},
					},
				},
			}, nil
		},
	})
	return resultToError(res)
}
