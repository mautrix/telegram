package connector

import (
	"context"
	"errors"
	"fmt"
	"slices"
	"strings"

	"github.com/rs/zerolog"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/simplevent"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/connector/store"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

func (tc *TelegramClient) GetUserInfo(ctx context.Context, ghost *bridgev2.Ghost) (*bridgev2.UserInfo, error) {
	peerType, id, err := ids.ParseUserID(ghost.ID)
	if err != nil {
		return nil, err
	}
	switch peerType {
	case ids.PeerTypeUser:
		if user, err := tc.getSingleUser(ctx, id); err != nil {
			return nil, fmt.Errorf("failed to get user %d: %w", id, err)
		} else {
			return tc.wrapUserInfo(ctx, user, ghost)
		}
	case ids.PeerTypeChannel:
		if channel, err := tc.getSingleChannel(ctx, id); err != nil {
			return nil, fmt.Errorf("failed to get channel %d: %w", id, err)
		} else {
			return tc.wrapChannelGhostInfo(ctx, channel)
		}
	default:
		return nil, fmt.Errorf("unexpected peer type: %s", peerType)
	}
}

func (tc *TelegramClient) getChatPeerForInputFromMessage(ctx context.Context, id int64, peerID tg.PeerClass) (tg.InputPeerClass, error) {
	switch typedChat := peerID.(type) {
	case *tg.PeerUser:
		if id == typedChat.UserID {
			// We don't have the user's access hash
			return nil, nil
		}
		accessHash, err := tc.ScopedStore.GetAccessHash(ctx, ids.PeerTypeUser, typedChat.UserID)
		if err != nil {
			return nil, err
		}
		return &tg.InputPeerUser{UserID: typedChat.UserID, AccessHash: accessHash}, nil
	case *tg.PeerChat:
		return &tg.InputPeerChat{ChatID: typedChat.ChatID}, nil
	case *tg.PeerChannel:
		if id == typedChat.ChannelID {
			// We don't have the channel's access hash
			return nil, nil
		}
		accessHash, err := tc.ScopedStore.GetAccessHash(ctx, ids.PeerTypeChannel, typedChat.ChannelID)
		if err != nil {
			return nil, err
		}
		return &tg.InputPeerChannel{ChannelID: typedChat.ChannelID, AccessHash: accessHash}, nil
	default:
		return nil, nil
	}
}

func (tc *TelegramClient) getInputUserFromContext(ctx context.Context, id int64) (*tg.InputUserFromMessage, error) {
	msg, ok := bridgev2.GetRemoteEventFromContext(ctx).(*simplevent.Message[*tg.Message])
	if !ok {
		return nil, nil
	}
	inputPeer, err := tc.getChatPeerForInputFromMessage(ctx, id, msg.Data.PeerID)
	if err != nil || inputPeer == nil {
		return nil, err
	}
	return &tg.InputUserFromMessage{
		Peer:   inputPeer,
		MsgID:  msg.Data.ID,
		UserID: id,
	}, nil
}

func (tc *TelegramClient) getInputChannelFromContext(ctx context.Context, id int64) (*tg.InputChannelFromMessage, error) {
	msg, ok := bridgev2.GetRemoteEventFromContext(ctx).(*simplevent.Message[*tg.Message])
	if !ok {
		return nil, nil
	}
	inputPeer, err := tc.getChatPeerForInputFromMessage(ctx, id, msg.Data.PeerID)
	if err != nil || inputPeer == nil {
		return nil, err
	}
	return &tg.InputChannelFromMessage{
		Peer:      inputPeer,
		MsgID:     msg.Data.ID,
		ChannelID: id,
	}, nil
}

func (tc *TelegramClient) getInputUser(ctx context.Context, id int64) (tg.InputUserClass, error) {
	accessHash, err := tc.ScopedStore.GetAccessHash(ctx, ids.PeerTypeUser, id)
	if errors.Is(err, store.ErrNoAccessHash) {
		fromMsg, fromMsgErr := tc.getInputUserFromContext(ctx, id)
		if fromMsgErr != nil {
			return nil, fmt.Errorf("%w, also failed to get from message: %w", err, fromMsgErr)
		} else if fromMsg == nil {
			return nil, err
		}
		zerolog.Ctx(ctx).Trace().
			Any("input_peer", fromMsg).
			Msg("Using InputUserFromMessage as access hash wasn't found")
		return fromMsg, nil
	} else if err != nil {
		return nil, fmt.Errorf("failed to get access hash for user %d: %w", id, err)
	}
	return &tg.InputUser{UserID: id, AccessHash: accessHash}, nil
}

func (tc *TelegramClient) getInputPeerUser(ctx context.Context, id int64) (*tg.InputPeerUser, error) {
	accessHash, err := tc.ScopedStore.GetAccessHash(ctx, ids.PeerTypeUser, id)
	if errors.Is(err, store.ErrNoAccessHash) {
		return nil, err
	} else if err != nil {
		return nil, fmt.Errorf("failed to get access hash for user %d: %w", id, err)
	}
	return &tg.InputPeerUser{UserID: id, AccessHash: accessHash}, nil
}

func (tc *TelegramClient) getSingleUser(ctx context.Context, id int64) (tg.UserClass, error) {
	if inputUser, err := tc.getInputUser(ctx, id); err != nil {
		return nil, err
	} else if users, err := tc.client.API().UsersGetUsers(ctx, []tg.InputUserClass{inputUser}); err != nil {
		return nil, err
	} else if len(users) == 0 {
		// TODO does this mean the user is deleted? Need to handle this a bit better
		return nil, fmt.Errorf("failed to get user info for user %d", id)
	} else {
		return users[0], nil
	}
}

func (tc *TelegramClient) getInputChannel(ctx context.Context, id int64) (tg.InputChannelClass, error) {
	accessHash, err := tc.ScopedStore.GetAccessHash(ctx, ids.PeerTypeChannel, id)
	if err != nil {
		fromMsg, fromMsgErr := tc.getInputChannelFromContext(ctx, id)
		if fromMsgErr != nil {
			return nil, fmt.Errorf("%w, also failed to get from message: %w", err, fromMsgErr)
		} else if fromMsg == nil {
			return nil, err
		}
		zerolog.Ctx(ctx).Trace().
			Any("input_peer", fromMsg).
			Msg("Using InputChannelFromMessage as access hash wasn't found")
		return fromMsg, nil
	}
	return &tg.InputChannel{ChannelID: id, AccessHash: accessHash}, nil
}

func (tc *TelegramClient) getSingleChannel(ctx context.Context, id int64) (*tg.Channel, error) {
	inputChannel, err := tc.getInputChannel(ctx, id)
	if err != nil {
		return nil, err
	}
	chats, err := APICallWithOnlyChatUpdates(ctx, tc, func() (tg.MessagesChatsClass, error) {
		return tc.client.API().ChannelsGetChannels(ctx, []tg.InputChannelClass{inputChannel})
	})
	if err != nil {
		return nil, err
	} else if len(chats.GetChats()) == 0 {
		return nil, fmt.Errorf("failed to get channel info for channel %d", id)
	} else if channel, ok := chats.GetChats()[0].(*tg.Channel); !ok {
		return nil, fmt.Errorf("unexpected channel type %T", chats.GetChats()[id])
	} else {
		return channel, nil
	}
}

func (tc *TelegramClient) wrapChannelGhostInfo(ctx context.Context, channel *tg.Channel) (*bridgev2.UserInfo, error) {
	var err error
	if accessHash, ok := channel.GetAccessHash(); ok && !channel.Min {
		if err = tc.ScopedStore.SetAccessHash(ctx, ids.PeerTypeChannel, channel.ID, accessHash); err != nil {
			return nil, err
		}
	}

	var avatar *bridgev2.Avatar
	avatar, err = tc.convertChatPhoto(channel.AsInputPeer(), channel.GetPhoto())
	if err != nil {
		return nil, err
	}

	var identifiers []string
	if username, set := channel.GetUsername(); set {
		err = tc.main.Store.Username.Set(ctx, ids.PeerTypeChannel, channel.ID, username)
		if err != nil {
			return nil, err
		}
		identifiers = append(identifiers, fmt.Sprintf("telegram:%s", username))
	}

	return &bridgev2.UserInfo{
		Name:        &channel.Title,
		Avatar:      avatar,
		Identifiers: identifiers,
	}, nil
}

func (tc *TelegramClient) wrapUserInfo(ctx context.Context, u tg.UserClass, ghost *bridgev2.Ghost) (*bridgev2.UserInfo, error) {
	oldMeta := ghost.Metadata.(*GhostMetadata)
	user, ok := u.(*tg.User)
	if !ok {
		return nil, fmt.Errorf("user is %T not *tg.User", user)
	}
	var identifiers []string
	if !user.Min {
		if accessHash, ok := user.GetAccessHash(); ok {
			if err := tc.ScopedStore.SetAccessHash(ctx, ids.PeerTypeUser, user.ID, accessHash); err != nil {
				return nil, err
			}
		}

		if err := tc.main.Store.Username.Set(ctx, ids.PeerTypeUser, user.ID, user.Username); err != nil {
			return nil, err
		}

		if user.Username != "" {
			identifiers = append(identifiers, fmt.Sprintf("telegram:%s", user.Username))
		}
		for _, username := range user.Usernames {
			identifiers = append(identifiers, fmt.Sprintf("telegram:%s", username.Username))
		}
		if phone, ok := user.GetPhone(); ok {
			normalized := strings.TrimPrefix(phone, "+")
			identifiers = append(identifiers, fmt.Sprintf("tel:+%s", normalized))
			if err := tc.main.Store.PhoneNumber.Set(ctx, user.ID, normalized); err != nil {
				return nil, err
			}
		}
	}
	slices.Sort(identifiers)
	identifiers = slices.Compact(identifiers)

	var avatar *bridgev2.Avatar
	photo, ok := user.Photo.(*tg.UserProfilePhoto)
	if ok &&
		(!user.Min || user.ApplyMinPhoto || oldMeta.IsMin()) &&
		// Hack: check ApplyMinPhoto in addition to Personal, because some personalized avatars
		// only have the ApplyMinPhoto flag and not Personal.
		((!photo.Personal && user.ApplyMinPhoto) || tc.main.Config.ContactAvatars) {
		var err error
		avatar, err = tc.convertUserProfilePhoto(ctx, user, photo)
		if err != nil {
			return nil, err
		}
	}

	name := tc.main.Config.FormatDisplayname(user.FirstName, user.LastName, user.Username, user.Deleted, user.ID)
	namePtr := &name
	if user.Contact && ghost.Name != "" && oldMeta.ContactSource != tc.telegramUserID && oldMeta.ContactSource != 0 && !tc.main.Config.ContactNames {
		namePtr = nil
	}
	if user.Min && !oldMeta.IsMin() && ghost.Name != "" {
		namePtr = nil
	}
	return &bridgev2.UserInfo{
		IsBot:       &user.Bot,
		Name:        namePtr,
		Avatar:      avatar,
		Identifiers: identifiers,
		ExtraUpdates: func(ctx context.Context, ghost *bridgev2.Ghost) (changed bool) {
			meta := ghost.Metadata.(*GhostMetadata)
			if !user.Min {
				changed = changed || meta.IsPremium != user.Premium || meta.Deleted != user.Deleted || meta.IsMin()
				meta.IsPremium = user.Premium
				meta.Deleted = user.Deleted
				meta.NotMin = true
				if meta.ContactSource == 0 || meta.ContactSource == tc.telegramUserID || (!user.Contact && meta.SourceIsContact) {
					changed = changed || meta.ContactSource != tc.telegramUserID || meta.SourceIsContact != user.Contact
					meta.ContactSource = tc.telegramUserID
					meta.SourceIsContact = user.Contact
				}
			}
			return changed
		},
	}, nil
}
