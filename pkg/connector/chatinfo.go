package connector

import (
	"context"
	"fmt"
	"time"

	"github.com/gotd/td/tg"
	"go.mau.fi/util/ptr"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/connector/media"
)

func (t *TelegramClient) getDMChatInfo(ctx context.Context, userID int64) (*bridgev2.ChatInfo, error) {
	chatInfo := bridgev2.ChatInfo{
		Type:        ptr.Ptr(database.RoomTypeDM),
		Members:     &bridgev2.ChatMemberList{IsFull: true},
		CanBackfill: true,
	}
	accessHash, found, err := t.ScopedStore.GetUserAccessHash(ctx, userID)
	if err != nil {
		return nil, fmt.Errorf("failed to get access hash for user %d: %w", userID, err)
	} else if !found {
		return nil, fmt.Errorf("access hash not found for user %d", userID)
	}
	users, err := t.client.API().UsersGetUsers(ctx, []tg.InputUserClass{&tg.InputUser{
		UserID:     userID,
		AccessHash: accessHash,
	}})
	if err != nil {
		return nil, err
	} else if len(users) == 0 {
		return nil, fmt.Errorf("failed to get user info for user %d", userID)
	} else if userInfo, err := t.getUserInfoFromTelegramUser(ctx, users[0]); err != nil {
		return nil, err
	} else if err = t.updateGhostWithUserInfo(ctx, userID, userInfo); err != nil {
		return nil, err
	} else {
		chatInfo.Members.Members = []bridgev2.ChatMember{
			{
				EventSender: bridgev2.EventSender{
					SenderLogin: ids.MakeUserLoginID(userID),
					Sender:      ids.MakeUserID(userID),
				},
				UserInfo: userInfo,
			},
			{EventSender: t.mySender()},
		}
	}
	return &chatInfo, nil
}

func (t *TelegramClient) getGroupChatInfo(ctx context.Context, fullChat *tg.MessagesChatFull, chatID int64) (*bridgev2.ChatInfo, bool, error) {
	if err := t.updateUsersFromResponse(ctx, fullChat); err != nil {
		return nil, false, err
	}

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
		Type: ptr.Ptr(database.RoomTypeGroupDM), // TODO Is this correct for channels?
		Members: &bridgev2.ChatMemberList{
			IsFull:  true,
			Members: []bridgev2.ChatMember{{EventSender: t.mySender()}},
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
	// fmt.Printf("get chat info %+v\n", portal)
	peerType, id, err := ids.ParsePortalID(portal.ID)
	if err != nil {
		return nil, err
	}

	switch peerType {
	case ids.PeerTypeUser:
		return t.getDMChatInfo(ctx, id)
	case ids.PeerTypeChat:
		fullChat, err := t.client.API().MessagesGetFullChat(ctx, id)
		if err != nil {
			return nil, err
		}
		chatInfo, _, err := t.getGroupChatInfo(ctx, fullChat, id)
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

			chatInfo.Members.Members = append(chatInfo.Members.Members, bridgev2.ChatMember{
				EventSender: bridgev2.EventSender{
					IsFromMe:    user.GetUserID() == t.telegramUserID,
					SenderLogin: ids.MakeUserLoginID(user.GetUserID()),
					Sender:      ids.MakeUserID(user.GetUserID()),
				},
			})

			if len(chatInfo.Members.Members) >= t.main.Config.MemberList.NormalizedMaxInitialSync() {
				break
			}
		}
		return chatInfo, nil
	case ids.PeerTypeChannel:
		accessHash, found, err := t.ScopedStore.GetChannelAccessHash(ctx, t.telegramUserID, id)
		if err != nil {
			return nil, fmt.Errorf("failed to get channel access hash: %w", err)
		} else if !found {
			return nil, fmt.Errorf("channel access hash not found for %d", id)
		}
		inputChannel := &tg.InputChannel{ChannelID: id, AccessHash: accessHash}
		fullChat, err := t.client.API().ChannelsGetFullChannel(ctx, inputChannel)
		if err != nil {
			return nil, err
		}

		chatInfo, isBroadcastChannel, err := t.getGroupChatInfo(ctx, fullChat, id)
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
			}
			chatInfo.Members.IsFull = len(participants.Participants) < limit
			if err := t.updateUsersFromResponse(ctx, participants); err != nil {
				return nil, err
			}
			chatInfo.Members.Members = append(chatInfo.Members.Members, t.filterChannelParticipants(participants.Participants, limit)...)
		} else {
			remaining := t.main.Config.MemberList.NormalizedMaxInitialSync()
			var offset int
			for remaining > 0 {
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
				}
				if len(participants.Participants) == 0 {
					chatInfo.Members.IsFull = true
					break
				}

				if err := t.updateUsersFromResponse(ctx, participants); err != nil {
					return nil, err
				}
				chatInfo.Members.Members = append(chatInfo.Members.Members, t.filterChannelParticipants(participants.Participants, limit)...)

				offset += len(participants.Participants)
				remaining -= len(participants.Participants)
			}
		}
		return chatInfo, nil
	default:
		panic(fmt.Sprintf("unsupported peer type %s", peerType))
	}
}
