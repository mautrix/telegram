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
	"context"
	"crypto/sha256"
	"fmt"
	"iter"
	"slices"
	"time"

	"github.com/rs/zerolog"
	"go.mau.fi/util/ptr"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/event"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

var (
	mutedPowerLevel      = ptr.Ptr(-1)
	anyonePowerLevel     = ptr.Ptr(0)
	modPowerLevel        = ptr.Ptr(50)
	superadminPowerLevel = ptr.Ptr(75)
	creatorPowerLevel    = ptr.Ptr(95)
	nobodyPowerLevel     = ptr.Ptr(99)

	otherPowerLevel          = ptr.Ptr(40)
	anonymousPowerLevel      = ptr.Ptr(41)
	postMessagesPowerLevel   = ptr.Ptr(42)
	editMessagesPowerLevel   = ptr.Ptr(43)
	deleteMessagesPowerLevel = ptr.Ptr(44)
	postStoriesPowerLevel    = ptr.Ptr(45)
	editStoriesPowerLevel    = ptr.Ptr(46)
	deleteStoriesPowerLevel  = ptr.Ptr(47)
	changeInfoPowerLevel     = ptr.Ptr(50)
	inviteUsersPowerLevel    = ptr.Ptr(51)
	manageCallPowerLevel     = ptr.Ptr(52)
	pinMessagesPowerLevel    = ptr.Ptr(53)
	manageTopicsPowerLevel   = ptr.Ptr(54)
	banUsersPowerLevel       = ptr.Ptr(55)
	addAdminsPowerLevel      = ptr.Ptr(60)
)

func adminRightsToPowerLevel(rights tg.ChatAdminRights) *int {
	if rights.AddAdmins {
		return addAdminsPowerLevel
	} else if rights.BanUsers {
		return banUsersPowerLevel
	} else if rights.ManageTopics {
		return manageTopicsPowerLevel
	} else if rights.PinMessages {
		return pinMessagesPowerLevel
	} else if rights.ManageCall {
		return manageCallPowerLevel
	} else if rights.InviteUsers {
		return inviteUsersPowerLevel
	} else if rights.ChangeInfo {
		return changeInfoPowerLevel
	} else if rights.DeleteStories {
		return deleteStoriesPowerLevel
	} else if rights.EditStories {
		return editStoriesPowerLevel
	} else if rights.PostStories {
		return postStoriesPowerLevel
	} else if rights.DeleteMessages {
		return deleteMessagesPowerLevel
	} else if rights.EditMessages {
		return editMessagesPowerLevel
	} else if rights.PostMessages {
		return postMessagesPowerLevel
	} else if rights.Anonymous {
		return anonymousPowerLevel
	}
	return otherPowerLevel
}

func (t *TelegramClient) getDMChatInfo(ctx context.Context, userID int64) (*bridgev2.ChatInfo, error) {
	ghost, err := t.main.Bridge.GetGhostByID(ctx, ids.MakeUserID(userID))
	if err != nil {
		return nil, err
	}

	chatInfo := bridgev2.ChatInfo{
		Type: ptr.Ptr(database.RoomTypeDM),
		Members: &bridgev2.ChatMemberList{
			IsFull:      true,
			MemberMap:   map[networkid.UserID]bridgev2.ChatMember{},
			PowerLevels: t.getDMPowerLevels(ghost),
		},
		CanBackfill:  !t.metadata.IsBot,
		ExtraUpdates: updatePortalLastSyncAt,
	}
	chatInfo.Members.MemberMap.Add(bridgev2.ChatMember{EventSender: t.mySender()})
	chatInfo.Members.MemberMap.Add(bridgev2.ChatMember{EventSender: t.senderForUserID(userID)})
	if userID == t.telegramUserID {
		chatInfo.Avatar = &bridgev2.Avatar{
			ID:     networkid.AvatarID(t.main.Config.SavedMessagesAvatar),
			Remove: len(t.main.Config.SavedMessagesAvatar) == 0,
			MXC:    t.main.Config.SavedMessagesAvatar,
			Hash:   sha256.Sum256([]byte(t.main.Config.SavedMessagesAvatar)),
		}
		chatInfo.Name = ptr.Ptr("Telegram Saved Messages")
		chatInfo.Topic = ptr.Ptr("Your Telegram cloud storage chat")
	}
	return &chatInfo, nil
}

func isBroadcastChannel(chat tg.ChatClass) bool {
	switch c := chat.(type) {
	case *tg.Channel:
		return c.Broadcast
	default:
		return false
	}
}

type memberFetchMeta struct {
	Input              *tg.InputChannel
	IsBroadcast        bool
	ParticipantsHidden bool
	IsForum            bool
}

func (t *TelegramClient) wrapChatInfo(portalID networkid.PortalID, rawChat tg.ChatClass) (*bridgev2.ChatInfo, *memberFetchMeta, error) {
	info := bridgev2.ChatInfo{
		Type:        ptr.Ptr(database.RoomTypeDefault),
		CanBackfill: !t.metadata.IsBot,
		Members: &bridgev2.ChatMemberList{
			ExcludeChangesFromTimeline: true,
			MemberMap:                  bridgev2.ChatMemberMap{},
		},
		ExcludeChangesFromTimeline: true,
	}
	var mfm memberFetchMeta
	var isMegagroup, isForumGeneral, left bool
	var avatarErr error
	switch chat := rawChat.(type) {
	case *tg.Chat:
		info.Name = &chat.Title
		info.Members.TotalMemberCount = chat.ParticipantsCount
		info.Avatar, avatarErr = t.convertChatPhoto(chat.AsInputPeer(), chat.Photo)
		info.Members.PowerLevels = t.getPowerLevelOverridesFromBannedRights(chat, chat.DefaultBannedRights)
		left = chat.Left
	case *tg.Channel:
		mfm.Input = chat.AsInput()
		mfm.IsBroadcast = chat.Broadcast
		info.Name = &chat.Title
		info.Members.TotalMemberCount = chat.ParticipantsCount
		isMegagroup = chat.Megagroup
		info.Avatar, avatarErr = t.convertChatPhoto(chat.AsInputPeer(), chat.Photo)
		info.Members.PowerLevels = t.getPowerLevelOverridesFromBannedRights(chat, chat.DefaultBannedRights)
		_, _, topicID, _ := ids.ParsePortalID(portalID)
		if chat.Forum {
			if topicID == ids.TopicIDSpaceRoom {
				info.Type = ptr.Ptr(database.RoomTypeSpace)
			} else if topicID == 0 {
				isForumGeneral = true
				info.Name = ptr.Ptr("#General - " + *info.Name)
			}
			if topicID != ids.TopicIDSpaceRoom {
				info.ParentID = ptr.Ptr(ids.MakeForumParentPortalID(chat.ID))
			}
			mfm.IsForum = true
		} else if topicID != 0 {
			return nil, nil, fmt.Errorf("channel %d is not a forum, cannot have topics", chat.GetID())
		}
		left = chat.Left
		if chat.Broadcast {
			info.Members.MemberMap.Set(bridgev2.ChatMember{
				EventSender: bridgev2.EventSender{Sender: ids.MakeChannelUserID(chat.GetID())},
				PowerLevel:  superadminPowerLevel,
			})
		} else if chat.Megagroup && !t.main.Config.ShouldBridge(chat.ParticipantsCount) {
			// TODO change this to a better error whenever that is implemented in mautrix-go
			return nil, nil, fmt.Errorf("too many participants (%d) in chat %d", chat.ParticipantsCount, chat.GetID())
		}
	default:
		return nil, nil, fmt.Errorf("unsupported chat type %T", rawChat)
	}
	if avatarErr != nil {
		return nil, nil, fmt.Errorf("failed to wrap chat avatar: %w", avatarErr)
	}
	if !left {
		info.Members.MemberMap.Add(bridgev2.ChatMember{EventSender: t.mySender()})
	}
	info.ExtraUpdates = func(ctx context.Context, portal *bridgev2.Portal) bool {
		meta := portal.Metadata.(*PortalMetadata)
		_ = updatePortalLastSyncAt(ctx, portal)
		changed := meta.SetIsSuperGroup(isMegagroup)
		changed = meta.SetIsForumGeneral(isForumGeneral) || changed
		if info.Members.TotalMemberCount != 0 && meta.ParticipantsCount != info.Members.TotalMemberCount {
			meta.ParticipantsCount = info.Members.TotalMemberCount
			changed = true
		}
		return changed
	}
	return &info, &mfm, nil
}

func (t *TelegramClient) overrideChatInfoWithTopic(info *bridgev2.ChatInfo, topic *tg.ForumTopic) {
	info.Name = ptr.Ptr(topic.Title + " - " + *info.Name)
	if topic.Closed {
		info.Members.PowerLevels.EventsDefault = nobodyPowerLevel
	}
}

func (t *TelegramClient) getChannelParticipants(ctx context.Context, req *tg.ChannelsGetParticipantsRequest) (*tg.ChannelsChannelParticipants, error) {
	return APICallWithUpdates(ctx, t, func() (*tg.ChannelsChannelParticipants, error) {
		p, err := t.client.API().ChannelsGetParticipants(ctx, req)
		if err != nil {
			return nil, err
		}
		participants, _ := p.(*tg.ChannelsChannelParticipants)
		return participants, nil
	})
}

func (t *TelegramClient) fillChannelMembers(ctx context.Context, mfm *memberFetchMeta, info *bridgev2.ChatMemberList) error {
	if mfm.Input == nil || mfm.ParticipantsHidden || (mfm.IsBroadcast && !t.main.Config.MemberList.SyncBroadcastChannels) {
		return nil
	}
	memberSyncLimit := t.main.Config.MemberList.NormalizedMaxInitialSync()

	if memberSyncLimit <= 200 {
		participants, err := t.getChannelParticipants(ctx, &tg.ChannelsGetParticipantsRequest{
			Channel: mfm.Input,
			Filter:  &tg.ChannelParticipantsRecent{},
			Limit:   memberSyncLimit,
		})
		if err != nil || participants == nil {
			return err
		}
		info.IsFull = len(participants.Participants) < memberSyncLimit &&
			len(participants.Participants) >= info.TotalMemberCount &&
			info.TotalMemberCount > 0
		for participant := range t.filterChannelParticipants(participants.Participants, memberSyncLimit) {
			info.MemberMap.Set(participant)
		}
	} else {
		remaining := memberSyncLimit
		var offset int
		for remaining > 0 {
			participants, err := t.getChannelParticipants(ctx, &tg.ChannelsGetParticipantsRequest{
				Channel: mfm.Input,
				Filter:  &tg.ChannelParticipantsSearch{},
				Limit:   min(remaining, 200),
				Offset:  offset,
			})
			if err != nil || participants == nil {
				return err
			}
			if len(participants.Participants) == 0 {
				info.IsFull = len(info.MemberMap) >= info.TotalMemberCount &&
					info.TotalMemberCount > 0
				break
			}

			for participant := range t.filterChannelParticipants(participants.Participants, remaining) {
				info.MemberMap.Set(participant)
			}

			offset += len(participants.Participants)
			remaining -= len(participants.Participants)
		}
	}
	return nil
}

func (t *TelegramClient) fillUserLocalMeta(info *bridgev2.ChatInfo, dialog *tg.Dialog) {
	info.UserLocal = &bridgev2.UserLocalPortalInfo{}
	if mu, ok := dialog.NotifySettings.GetMuteUntil(); ok {
		info.UserLocal.MutedUntil = ptr.Ptr(time.Unix(int64(mu), 0))
	} else {
		info.UserLocal.MutedUntil = &bridgev2.Unmuted
	}
	if dialog.Pinned {
		info.UserLocal.Tag = ptr.Ptr(event.RoomTagFavourite)
	}
}

func (t *TelegramClient) wrapFullChatInfo(portalID networkid.PortalID, fullChat *tg.MessagesChatFull) (*bridgev2.ChatInfo, *memberFetchMeta, error) {
	var chat tg.ChatClass
	for _, c := range fullChat.GetChats() {
		if c.GetID() == fullChat.FullChat.GetID() {
			chat = c
			break
		}
	}
	if chat == nil {
		return nil, nil, fmt.Errorf("chat ID %d not found in full chat", fullChat.FullChat.GetID())
	}

	info, mfm, err := t.wrapChatInfo(portalID, chat)
	if err != nil {
		return nil, nil, err
	}

	var newAllowedReactions []string
	if reactions, ok := fullChat.FullChat.GetAvailableReactions(); ok {
		switch typedReactions := reactions.(type) {
		case *tg.ChatReactionsAll:
			newAllowedReactions = nil
		case *tg.ChatReactionsNone:
			newAllowedReactions = []string{}
		case *tg.ChatReactionsSome:
			newAllowedReactions = make([]string, 0, len(typedReactions.Reactions))
			for _, react := range typedReactions.Reactions {
				emoji, ok := react.(*tg.ReactionEmoji)
				if ok {
					newAllowedReactions = append(newAllowedReactions, emoji.Emoticon)
				}
			}
			slices.Sort(newAllowedReactions)
		}
	}
	if ttl, ok := fullChat.FullChat.GetTTLPeriod(); ok {
		info.Disappear = &database.DisappearingSetting{
			Type:  event.DisappearingTypeAfterSend,
			Timer: time.Duration(ttl) * time.Second,
		}
	}
	if about := fullChat.FullChat.GetAbout(); about != "" {
		info.Topic = &about
	}
	info.ExtraUpdates = bridgev2.MergeExtraUpdaters(
		info.ExtraUpdates,
		reactionUpdater(newAllowedReactions),
		markFullSynced,
	)

	switch typedFullChat := fullChat.FullChat.(type) {
	case *tg.ChatFull:
		participants, _ := typedFullChat.GetParticipants().(*tg.ChatParticipants)
		memberSyncLimit := t.main.Config.MemberList.NormalizedMaxInitialSync()
		info.Members.IsFull = true
		for i, user := range participants.GetParticipants() {
			var powerLevel *int
			switch user.(type) {
			case *tg.ChatParticipantCreator:
				powerLevel = creatorPowerLevel
			case *tg.ChatParticipantAdmin:
				powerLevel = modPowerLevel
			default:
				powerLevel = ptr.Ptr(0)
			}

			info.Members.MemberMap.Set(bridgev2.ChatMember{
				EventSender: t.senderForUserID(user.GetUserID()),
				PowerLevel:  powerLevel,
			})

			if i >= memberSyncLimit {
				info.Members.IsFull = false
				break
			}
		}
	case *tg.ChannelFull:
		mfm.ParticipantsHidden = !typedFullChat.CanViewParticipants || typedFullChat.ParticipantsHidden
	}

	return info, mfm, nil
}

func reactionUpdater(newAllowedReactions []string) bridgev2.ExtraUpdater[*bridgev2.Portal] {
	return func(ctx context.Context, portal *bridgev2.Portal) bool {
		meta := portal.Metadata.(*PortalMetadata)
		if newAllowedReactions == nil {
			if meta.AllowedReactions == nil {
				return false
			}
			meta.AllowedReactions = nil
			return true
		}
		if meta.AllowedReactions == nil || !slices.Equal(newAllowedReactions, meta.AllowedReactions) {
			meta.AllowedReactions = newAllowedReactions
			return true
		}
		return false
	}
}

func markFullSynced(ctx context.Context, portal *bridgev2.Portal) bool {
	meta := portal.Metadata.(*PortalMetadata)
	if !meta.FullSynced {
		meta.FullSynced = true
		return true
	}
	return false
}

func (t *TelegramClient) avatarFromPhoto(ctx context.Context, peerType ids.PeerType, peerID int64, photo tg.PhotoClass) *bridgev2.Avatar {
	if photo == nil {
		zerolog.Ctx(ctx).Trace().Msg("Chat photo is nil, returning no avatar")
		return nil
	} else if photo.TypeID() != tg.PhotoTypeID {
		zerolog.Ctx(ctx).Debug().Str("type_name", photo.TypeName()).Msg("Chat photo type unknown, returning no avatar")
		return nil
	}
	avatar, err := t.convertPhoto(ctx, peerType, peerID, photo)
	if err != nil {
		zerolog.Ctx(ctx).Err(err).Int64("id", photo.GetID()).Msg("Failed to convert avatar")
		return nil
	}
	return avatar
}

func (t *TelegramClient) filterChannelParticipants(participants []tg.ChannelParticipantClass, limit int) iter.Seq[bridgev2.ChatMember] {
	return func(yield func(bridgev2.ChatMember) bool) {
		for i, u := range participants {
			var member bridgev2.ChatMember
			switch participant := u.(type) {
			case *tg.ChannelParticipant:
				member.EventSender = t.senderForUserID(participant.GetUserID())
				member.PowerLevel = anyonePowerLevel
			case *tg.ChannelParticipantSelf:
				member.EventSender = t.senderForUserID(participant.GetUserID())
				member.PowerLevel = anyonePowerLevel
			case *tg.ChannelParticipantCreator:
				member.EventSender = t.senderForUserID(participant.GetUserID())
				member.PowerLevel = creatorPowerLevel
			case *tg.ChannelParticipantAdmin:
				member.EventSender = t.senderForUserID(participant.GetUserID())
				member.PowerLevel = adminRightsToPowerLevel(participant.AdminRights)
			case *tg.ChannelParticipantBanned:
				if participant.BannedRights.ViewMessages {
					member.Membership = event.MembershipBan
				} else if participant.Left {
					member.Membership = event.MembershipLeave
				}
				if participant.BannedRights.SendMessages {
					member.PowerLevel = mutedPowerLevel
				} else {
					member.PowerLevel = anyonePowerLevel
				}
				member.EventSender = t.getPeerSender(participant.GetPeer())
				member.MemberSender = t.senderForUserID(participant.GetKickedBy())
			case *tg.ChannelParticipantLeft:
				member.Membership = event.MembershipLeave
				member.PrevMembership = event.MembershipJoin
				member.EventSender = t.getPeerSender(participant.GetPeer())
			default:
				// TODO warning log?
				continue
			}
			if i >= limit && member.Membership == "" && !member.EventSender.IsFromMe {
				continue
			}

			if !yield(member) {
				return
			}
		}
	}
}

func (t *TelegramClient) GetChatInfo(ctx context.Context, portal *bridgev2.Portal) (*bridgev2.ChatInfo, error) {
	peerType, id, topicID, err := ids.ParsePortalID(portal.ID)
	if err != nil {
		return nil, err
	}

	switch peerType {
	case ids.PeerTypeUser:
		return t.getDMChatInfo(ctx, id)
	case ids.PeerTypeChat:
		fullChat, err := APICallWithUpdates(ctx, t, func() (*tg.MessagesChatFull, error) {
			return t.client.API().MessagesGetFullChat(ctx, id)
		})
		if err != nil {
			return nil, err
		}
		info, _, err := t.wrapFullChatInfo(portal.ID, fullChat)
		return info, err
	case ids.PeerTypeChannel:
		accessHash, err := t.ScopedStore.GetAccessHash(ctx, ids.PeerTypeChannel, id)
		if err != nil {
			return nil, fmt.Errorf("failed to get channel access hash: %w", err)
		}
		if topicID > 0 {
			resp, err := APICallWithUpdates(ctx, t, func() (*tg.MessagesForumTopics, error) {
				return t.client.API().MessagesGetForumTopicsByID(ctx, &tg.MessagesGetForumTopicsByIDRequest{
					Peer:   &tg.InputPeerChannel{ChannelID: id, AccessHash: accessHash},
					Topics: []int{topicID},
				})
			})
			if err != nil {
				return nil, err
			}
			channel, topic, err := getTopicInfoFromResponse(resp, id, topicID)
			if err != nil {
				return nil, err
			}
			info, _, err := t.wrapChatInfo(portal.ID, channel)
			if err != nil {
				return nil, err
			}
			t.overrideChatInfoWithTopic(info, topic)
			return info, nil
		}
		fullChat, err := APICallWithUpdates(ctx, t, func() (*tg.MessagesChatFull, error) {
			return t.client.API().ChannelsGetFullChannel(ctx, &tg.InputChannel{ChannelID: id, AccessHash: accessHash})
		})
		if err != nil {
			return nil, err
		}
		info, mfm, err := t.wrapFullChatInfo(portal.ID, fullChat)
		if err != nil {
			return nil, err
		}
		err = t.fillChannelMembers(ctx, mfm, info.Members)
		if err != nil {
			zerolog.Ctx(ctx).Err(err).Msg("Failed to get channel members")
		}
		return info, nil
	default:
		return nil, fmt.Errorf("unsupported peer type %s", peerType)
	}
}

func getTopicInfoFromResponse(resp *tg.MessagesForumTopics, channelID int64, topicID int) (channel *tg.Channel, topic *tg.ForumTopic, err error) {
	var ok bool
	for _, ch := range resp.GetChats() {
		if ch.GetID() == channelID {
			channel, ok = ch.(*tg.Channel)
			if !ok {
				return nil, nil, fmt.Errorf("chat ID %d is %T not *tg.Channel", channelID, ch)
			}
			break
		}
	}
	if channel == nil {
		return nil, nil, fmt.Errorf("channel ID %d not found in chats", channelID)
	}
	for _, tp := range resp.GetTopics() {
		if tp.GetID() == topicID {
			topic, ok = tp.(*tg.ForumTopic)
			if !ok {
				return nil, nil, fmt.Errorf("topic ID %d is %T not *tg.ForumTopic", topicID, tp)
			}
			break
		}
	}
	if topic == nil {
		return nil, nil, fmt.Errorf("topic ID %d not found in topics", topicID)
	}
	return
}

func (t *TelegramClient) getDMPowerLevels(ghost *bridgev2.Ghost) *bridgev2.PowerLevelOverrides {
	var plo bridgev2.PowerLevelOverrides
	// TODO use per-login metadata for blocked status
	if /*ghost.Metadata.(*GhostMetadata).Blocked*/ false {
		// Don't allow sending messages to blocked users
		plo.EventsDefault = superadminPowerLevel
	} else {
		plo.EventsDefault = anyonePowerLevel
	}
	plo.Events = map[event.Type]int{
		event.StateRoomName:                0,
		event.StateRoomAvatar:              0,
		event.StateTopic:                   0,
		event.StateBeeperDisappearingTimer: 0,
		event.BeeperDeleteChat:             0,
	}
	return &plo
}

func (t *TelegramClient) getPowerLevelOverridesFromBannedRights(entity tg.ChatClass, dbr tg.ChatBannedRights) *bridgev2.PowerLevelOverrides {
	var plo bridgev2.PowerLevelOverrides
	plo.Ban = banUsersPowerLevel
	plo.Kick = banUsersPowerLevel
	plo.Redact = deleteMessagesPowerLevel
	if dbr.InviteUsers {
		plo.Invite = inviteUsersPowerLevel
	} else {
		plo.Invite = anyonePowerLevel
	}
	plo.StateDefault = superadminPowerLevel
	plo.UsersDefault = anyonePowerLevel
	if c, ok := entity.(*tg.Channel); (ok && !c.Megagroup) || dbr.SendMessages {
		plo.EventsDefault = postMessagesPowerLevel
	} else {
		plo.EventsDefault = anyonePowerLevel
	}

	plo.Events = map[event.Type]int{
		event.StateEncryption:              99,
		event.StateTombstone:               99,
		event.StatePowerLevels:             85,
		event.StateHistoryVisibility:       85,
		event.StateBeeperDisappearingTimer: 85,
		event.BeeperDeleteChat:             *creatorPowerLevel,
	}

	if dbr.ChangeInfo {
		plo.Events[event.StateRoomName] = *changeInfoPowerLevel
		plo.Events[event.StateRoomAvatar] = *changeInfoPowerLevel
		plo.Events[event.StateTopic] = *changeInfoPowerLevel
		plo.Events[event.StateBeeperDisappearingTimer] = *changeInfoPowerLevel
	} else {
		plo.Events[event.StateRoomName] = 0
		plo.Events[event.StateRoomAvatar] = 0
		plo.Events[event.StateTopic] = 0
		plo.Events[event.StateBeeperDisappearingTimer] = 0
	}

	if dbr.PinMessages {
		plo.Events[event.StatePinnedEvents] = *pinMessagesPowerLevel
	} else {
		plo.Events[event.StatePinnedEvents] = 0
	}

	if dbr.SendStickers {
		plo.Events[event.EventSticker] = *postMessagesPowerLevel
	} else {
		plo.Events[event.EventSticker] = 0
	}

	return &plo
}
