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
	"go.mau.fi/mautrix-telegram/pkg/connector/util"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

func (t *TelegramClient) GetUserInfo(ctx context.Context, ghost *bridgev2.Ghost) (*bridgev2.UserInfo, error) {
	peerType, id, err := ids.ParseUserID(ghost.ID)
	if err != nil {
		return nil, err
	}
	switch peerType {
	case ids.PeerTypeUser:
		if user, err := t.getSingleUser(ctx, id); err != nil {
			return nil, fmt.Errorf("failed to get user %d: %w", id, err)
		} else {
			return t.wrapUserInfo(ctx, user, ghost)
		}
	case ids.PeerTypeChannel:
		if channel, err := t.getSingleChannel(ctx, id); err != nil {
			return nil, fmt.Errorf("failed to get channel %d: %w", id, err)
		} else {
			return t.wrapChannelGhostInfo(ctx, channel)
		}
	default:
		return nil, fmt.Errorf("unexpected peer type: %s", peerType)
	}
}

func (t *TelegramClient) getInputUserFromContext(ctx context.Context, id int64) (*tg.InputUserFromMessage, error) {
	msg, ok := bridgev2.GetRemoteEventFromContext(ctx).(*simplevent.Message[*tg.Message])
	if !ok {
		return nil, nil
	}
	fromUser, ok := msg.Data.FromID.(*tg.PeerUser)
	if !ok || fromUser.UserID != id {
		return nil, nil
	}
	var inputPeer tg.InputPeerClass
	switch typedChat := msg.Data.PeerID.(type) {
	case *tg.PeerUser:
		// We don't have the user's access hash
		return nil, nil
	case *tg.PeerChat:
		inputPeer = &tg.InputPeerChat{ChatID: typedChat.ChatID}
	case *tg.PeerChannel:
		accessHash, err := t.ScopedStore.GetAccessHash(ctx, ids.PeerTypeChannel, typedChat.ChannelID)
		if err != nil {
			return nil, err
		}
		inputPeer = &tg.InputPeerChannel{ChannelID: typedChat.ChannelID, AccessHash: accessHash}
	}
	return &tg.InputUserFromMessage{
		Peer:   inputPeer,
		MsgID:  msg.Data.ID,
		UserID: fromUser.UserID,
	}, nil
}

func (t *TelegramClient) getInputUser(ctx context.Context, id int64) (tg.InputUserClass, error) {
	accessHash, err := t.ScopedStore.GetAccessHash(ctx, ids.PeerTypeUser, id)
	if errors.Is(err, store.ErrNoAccessHash) {
		fromMsg, fromMsgErr := t.getInputUserFromContext(ctx, id)
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

func (t *TelegramClient) getInputPeerUser(ctx context.Context, id int64) (*tg.InputPeerUser, error) {
	accessHash, err := t.ScopedStore.GetAccessHash(ctx, ids.PeerTypeUser, id)
	if errors.Is(err, store.ErrNoAccessHash) {
		return nil, err
	} else if err != nil {
		return nil, fmt.Errorf("failed to get access hash for user %d: %w", id, err)
	}
	return &tg.InputPeerUser{UserID: id, AccessHash: accessHash}, nil
}

func (t *TelegramClient) getSingleUser(ctx context.Context, id int64) (tg.UserClass, error) {
	if inputUser, err := t.getInputUser(ctx, id); err != nil {
		return nil, err
	} else if users, err := t.client.API().UsersGetUsers(ctx, []tg.InputUserClass{inputUser}); err != nil {
		return nil, err
	} else if len(users) == 0 {
		// TODO does this mean the user is deleted? Need to handle this a bit better
		return nil, fmt.Errorf("failed to get user info for user %d", id)
	} else {
		return users[0], nil
	}
}

func (t *TelegramClient) wrapChannelGhostInfo(ctx context.Context, channel *tg.Channel) (*bridgev2.UserInfo, error) {
	var err error
	if accessHash, ok := channel.GetAccessHash(); ok && !channel.Min {
		if err = t.ScopedStore.SetAccessHash(ctx, ids.PeerTypeChannel, channel.ID, accessHash); err != nil {
			return nil, err
		}
	}

	if !channel.Broadcast {
		return nil, nil
	}

	var avatar *bridgev2.Avatar
	avatar, err = t.convertChatPhoto(channel.AsInputPeer(), channel.GetPhoto())
	if err != nil {
		return nil, err
	}

	var identifiers []string
	if username, set := channel.GetUsername(); set {
		err = t.main.Store.Username.Set(ctx, ids.PeerTypeChannel, channel.ID, username)
		if err != nil {
			return nil, err
		}
		identifiers = append(identifiers, fmt.Sprintf("telegram:%s", username))
	}

	return &bridgev2.UserInfo{
		Name:        &channel.Title,
		Avatar:      avatar,
		Identifiers: identifiers,
		ExtraUpdates: func(ctx context.Context, g *bridgev2.Ghost) bool {
			updated := !g.Metadata.(*GhostMetadata).IsChannel
			g.Metadata.(*GhostMetadata).IsChannel = true
			return updated
		},
	}, nil
}

func (t *TelegramClient) wrapUserInfo(ctx context.Context, u tg.UserClass, ghost *bridgev2.Ghost) (*bridgev2.UserInfo, error) {
	oldMeta := ghost.Metadata.(*GhostMetadata)
	user, ok := u.(*tg.User)
	if !ok {
		return nil, fmt.Errorf("user is %T not *tg.User", user)
	}
	var identifiers []string
	if !user.Min {
		if accessHash, ok := user.GetAccessHash(); ok {
			if err := t.ScopedStore.SetAccessHash(ctx, ids.PeerTypeUser, user.ID, accessHash); err != nil {
				return nil, err
			}
		}

		if err := t.main.Store.Username.Set(ctx, ids.PeerTypeUser, user.ID, user.Username); err != nil {
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
			if err := t.main.Store.PhoneNumber.Set(ctx, user.ID, normalized); err != nil {
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
		(!photo.Personal || t.main.Config.ContactAvatars) {
		var err error
		avatar, err = t.convertUserProfilePhoto(ctx, user, photo)
		if err != nil {
			return nil, err
		}
	}

	name := util.FormatFullName(user.FirstName, user.LastName, user.Deleted, user.ID)
	namePtr := &name
	if user.Contact && ghost.Name != "" && oldMeta.ContactSource != t.telegramUserID && oldMeta.ContactSource != 0 && !t.main.Config.ContactNames {
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
				changed = changed || meta.IsPremium != user.Premium || meta.IsBot != user.Bot || meta.IsMin()
				meta.IsPremium = user.Premium
				meta.IsBot = user.Bot
				meta.Deleted = user.Deleted
				meta.NotMin = true
				if meta.ContactSource == 0 || meta.ContactSource == t.telegramUserID || (!user.Contact && meta.SourceIsContact) {
					changed = changed || meta.ContactSource != t.telegramUserID || meta.SourceIsContact != user.Contact
					meta.ContactSource = t.telegramUserID
					meta.SourceIsContact = user.Contact
				}
			}
			return changed
		},
	}, nil
}
