package connector

import (
	"context"
	"fmt"
	"time"

	"github.com/gotd/td/tg"
	"go.mau.fi/util/ptr"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/networkid"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/connector/media"
)

func (t *TelegramClient) getDMChatInfo(userID int64) (*bridgev2.ChatInfo, error) {
	networkUserID := ids.MakeUserID(userID)
	chatInfo := bridgev2.ChatInfo{
		Type: ptr.Ptr(database.RoomTypeDM),
		Members: &bridgev2.ChatMemberList{
			IsFull: true,
			MemberMap: map[networkid.UserID]bridgev2.ChatMember{
				networkUserID: {
					EventSender: bridgev2.EventSender{
						SenderLogin: ids.MakeUserLoginID(userID),
						Sender:      networkUserID,
					},
				},
				t.userID: {EventSender: t.mySender()},
			},
		},
		CanBackfill: true,
	}
	if userID == t.telegramUserID {
		// TODO also hardcode the avatar used by telegram?
		chatInfo.Name = ptr.Ptr("Saved Messages")
	}
	return &chatInfo, nil
}

func (t *TelegramClient) getGroupChatInfo(fullChat *tg.MessagesChatFull, chatID int64) (*bridgev2.ChatInfo, bool, error) {
	var name *string
	var isBroadcastChannel, isMegagroup bool
	for _, c := range fullChat.GetChats() {
		if c.GetID() == chatID {
			switch chat := c.(type) {
			case *tg.Chat:
				name = &chat.Title
			case *tg.Channel:
				name = &chat.Title
				isBroadcastChannel = chat.Broadcast
				isMegagroup = chat.Megagroup
			}
			break
		}
	}

	chatInfo := bridgev2.ChatInfo{
		Name: name,
		Type: ptr.Ptr(database.RoomTypeDefault),
		Members: &bridgev2.ChatMemberList{
			IsFull:    true,
			MemberMap: map[networkid.UserID]bridgev2.ChatMember{},
		},
		CanBackfill: true,
		ExtraUpdates: func(ctx context.Context, p *bridgev2.Portal) bool {
			meta := p.Metadata.(*PortalMetadata)
			changed := meta.IsSuperGroup != isMegagroup
			meta.IsSuperGroup = isMegagroup
			return changed
		},
	}

	if ttl, ok := fullChat.FullChat.GetTTLPeriod(); ok {
		chatInfo.Disappear = &database.DisappearingSetting{
			Type:  database.DisappearingTypeAfterSend,
			Timer: time.Duration(ttl) * time.Second,
		}
	}

	if about := fullChat.FullChat.GetAbout(); about != "" {
		chatInfo.Topic = &about
	}

	return &chatInfo, isBroadcastChannel, nil
}

func (t *TelegramClient) avatarFromPhoto(photo tg.PhotoClass) *bridgev2.Avatar {
	if photo == nil || photo.TypeID() != tg.PhotoTypeID {
		return nil
	}
	return &bridgev2.Avatar{
		ID: ids.MakeAvatarID(photo.GetID()),
		Get: func(ctx context.Context) (data []byte, err error) {
			data, _, err = media.NewTransferer(t.client.API()).WithPhoto(photo).Download(ctx)
			return
		},
	}
}

func (t *TelegramClient) filterChannelParticipants(chatParticipants []tg.ChannelParticipantClass, limit int) (members []bridgev2.ChatMember) {
	for _, u := range chatParticipants {
		userIDable, ok := u.(interface{ GetUserID() int64 })
		if !ok {
			continue
		}

		members = append(members, bridgev2.ChatMember{
			EventSender: bridgev2.EventSender{
				IsFromMe:    userIDable.GetUserID() == t.telegramUserID,
				SenderLogin: ids.MakeUserLoginID(userIDable.GetUserID()),
				Sender:      ids.MakeUserID(userIDable.GetUserID()),
			},
		})

		if len(members) >= limit {
			break
		}
	}
	return
}

func (t *TelegramClient) GetChatInfo(ctx context.Context, portal *bridgev2.Portal) (*bridgev2.ChatInfo, error) {
	// FIXME GetFullChat should be avoided. Using only bundled info should be preferred whenever possible
	//       (e.g. when syncing dialogs, only use the data in the dialog list, don't fetch each chat info separately).
	peerType, id, err := ids.ParsePortalID(portal.ID)
	if err != nil {
		return nil, err
	}

	switch peerType {
	case ids.PeerTypeUser:
		return t.getDMChatInfo(id)
	case ids.PeerTypeChat:
		fullChat, err := APICallWithUpdates(ctx, t, func() (*tg.MessagesChatFull, error) {
			return t.client.API().MessagesGetFullChat(ctx, id)
		})
		if err != nil {
			return nil, err
		}
		chatInfo, _, err := t.getGroupChatInfo(fullChat, id)
		if err != nil {
			return nil, err
		}

		chatFull, ok := fullChat.FullChat.(*tg.ChatFull)
		if !ok {
			return nil, fmt.Errorf("full chat is %T not *tg.ChatFull", fullChat.FullChat)
		}
		chatInfo.Avatar = t.avatarFromPhoto(chatFull.ChatPhoto)

		if chatFull.Participants.TypeID() == tg.ChatParticipantsForbiddenTypeID {
			chatInfo.Members.IsFull = false
			return chatInfo, nil
		}
		chatParticipants := chatFull.Participants.(*tg.ChatParticipants)

		if !t.main.Config.ShouldBridge(len(chatParticipants.Participants)) {
			// TODO change this to a better error whenever that is implemented in mautrix-go
			return nil, fmt.Errorf("too many participants (%d) in chat %d", len(chatParticipants.Participants), id)
		}

		for _, user := range chatParticipants.GetParticipants() {
			if user.TypeID() == tg.ChannelParticipantBannedTypeID {
				continue
			}

			sender := ids.MakeUserID(user.GetUserID())
			chatInfo.Members.MemberMap[sender] = bridgev2.ChatMember{
				EventSender: bridgev2.EventSender{
					IsFromMe:    user.GetUserID() == t.telegramUserID,
					SenderLogin: ids.MakeUserLoginID(user.GetUserID()),
					Sender:      sender,
				},
			}

			if len(chatInfo.Members.MemberMap) >= t.main.Config.MemberList.NormalizedMaxInitialSync() {
				break
			}
		}
		return chatInfo, nil
	case ids.PeerTypeChannel:
		accessHash, err := t.ScopedStore.GetAccessHash(ctx, ids.PeerTypeChannel, id)
		if err != nil {
			return nil, fmt.Errorf("failed to get channel access hash: %w", err)
		}
		inputChannel := &tg.InputChannel{ChannelID: id, AccessHash: accessHash}
		fullChat, err := APICallWithUpdates(ctx, t, func() (*tg.MessagesChatFull, error) {
			return t.client.API().ChannelsGetFullChannel(ctx, inputChannel)
		})
		if err != nil {
			return nil, err
		}

		chatInfo, isBroadcastChannel, err := t.getGroupChatInfo(fullChat, id)
		if err != nil {
			return nil, err
		}

		channelFull, ok := fullChat.FullChat.(*tg.ChannelFull)
		if !ok {
			return nil, fmt.Errorf("full chat is %T not *tg.ChannelFull", fullChat.FullChat)
		}

		if !t.main.Config.ShouldBridge(channelFull.ParticipantsCount) {
			// TODO change this to a better error whenever that is implemented in mautrix-go
			return nil, fmt.Errorf("too many participants (%d) in chat %d", channelFull.ParticipantsCount, id)
		}

		chatInfo.Avatar = t.avatarFromPhoto(channelFull.ChatPhoto)

		// TODO save available reactions?
		// TODO save reactions limit?
		// TODO save emojiset?

		chatInfo.Members.IsFull = false
		if !portal.Metadata.(*PortalMetadata).IsSuperGroup {
			// Add the channel user
			sender := ids.MakeUserID(id)
			chatInfo.Members.MemberMap[sender] = bridgev2.ChatMember{
				EventSender: bridgev2.EventSender{
					SenderLogin: ids.MakeUserLoginID(id),
					Sender:      sender,
				},
			}
		}

		// Just return the current user as a member if we can't view the
		// participants or the max initial sync is 0.
		if t.main.Config.MemberList.MaxInitialSync == 0 || !channelFull.CanViewParticipants || channelFull.ParticipantsHidden {
			return chatInfo, nil
		}

		// If this is a broadcast channel and we're not syncing broadcast
		// channels, just return the chat info without all of the participant
		// info.
		if isBroadcastChannel && !t.main.Config.MemberList.SyncBroadcastChannels {
			return chatInfo, nil
		}

		limit := t.main.Config.MemberList.NormalizedMaxInitialSync()
		if limit <= 200 {
			participants, err := APICallWithUpdates(ctx, t, func() (*tg.ChannelsChannelParticipants, error) {
				p, err := t.client.API().ChannelsGetParticipants(ctx, &tg.ChannelsGetParticipantsRequest{
					Channel: inputChannel,
					Filter:  &tg.ChannelParticipantsRecent{},
					Limit:   limit,
				})
				if err != nil {
					return nil, err
				}
				participants, ok := p.(*tg.ChannelsChannelParticipants)
				if !ok {
					return nil, fmt.Errorf("returned participants is %T not *tg.ChannelsChannelParticipants", p)
				} else {
					return participants, nil
				}
			})
			if err != nil {
				return nil, err
			}
			chatInfo.Members.IsFull = len(participants.Participants) < limit
			for _, participant := range t.filterChannelParticipants(participants.Participants, limit) {
				chatInfo.Members.MemberMap[participant.Sender] = participant
			}
		} else {
			remaining := t.main.Config.MemberList.NormalizedMaxInitialSync()
			var offset int
			for remaining > 0 {
				participants, err := APICallWithUpdates(ctx, t, func() (*tg.ChannelsChannelParticipants, error) {
					p, err := t.client.API().ChannelsGetParticipants(ctx, &tg.ChannelsGetParticipantsRequest{
						Channel: inputChannel,
						Filter:  &tg.ChannelParticipantsSearch{},
						Limit:   min(remaining, 200),
						Offset:  offset,
					})
					if err != nil {
						return nil, err
					}
					participants, ok := p.(*tg.ChannelsChannelParticipants)
					if !ok {
						return nil, fmt.Errorf("returned participants is %T not *tg.ChannelsChannelParticipants", p)
					} else {
						return participants, nil
					}
				})
				if err != nil {
					return nil, err
				}
				if len(participants.Participants) == 0 {
					chatInfo.Members.IsFull = true
					break
				}

				for _, participant := range t.filterChannelParticipants(participants.Participants, limit) {
					chatInfo.Members.MemberMap[participant.Sender] = participant
				}

				offset += len(participants.Participants)
				remaining -= len(participants.Participants)
			}
		}
		return chatInfo, nil
	default:
		panic(fmt.Sprintf("unsupported peer type %s", peerType))
	}
}
