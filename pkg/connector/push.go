package connector

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"time"

	"github.com/gotd/td/bin"
	"github.com/gotd/td/crypto"
	"github.com/gotd/td/tg"
	"github.com/tidwall/gjson"
	"go.mau.fi/util/exslices"
	"go.mau.fi/util/random"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/networkid"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
)

var (
	_ bridgev2.PushableNetworkAPI = (*TelegramClient)(nil)
	_ bridgev2.PushParsingNetwork = (*TelegramConnector)(nil)
)

var PushAppSandbox = false

type PushCustomData struct {
	MessageID int `json:"msg_id"`

	ChannelID int64 `json:"channel_id"`
	ChatID    int64 `json:"chat_id"`
	FromID    int64 `json:"from_id"`

	ChatFromBroadcastID int64 `json:"chat_from_broadcast_id"`
	ChatFromGroupID     int64 `json:"chat_from_group_id"`
	ChatFromID          int64 `json:"chat_from_id"`
}

type PushNotificationData struct {
	LocKey  string         `json:"loc_key"`
	LocArgs []string       `json:"loc_args"`
	Custom  PushCustomData `json:"custom"`
	Sound   string         `json:"sound"`
	UserID  int            `json:"user_id"`
}

var PushMessageFormats = map[string]string{
	"AUTH_REGION":                    "New login from unrecognized device %[1]v, location: %[2]v",
	"AUTH_UNKNOWN":                   "New login from unrecognized device %[1]v",
	"CHANNEL_MESSAGES":               "%[1]v posted an album",
	"CHANNEL_MESSAGE_AUDIO":          "%[1]v posted a voice message",
	"CHANNEL_MESSAGE_CONTACT":        "%[1]v posted a contact %[2]v",
	"CHANNEL_MESSAGE_DOC":            "%[1]v posted a file",
	"CHANNEL_MESSAGE_DOCS":           "%[1]v posted %[2]v files",
	"CHANNEL_MESSAGE_FWDS":           "%[1]v posted %[2]v forwarded messages",
	"CHANNEL_MESSAGE_GAME":           "%[1]v invited you to play %[2]v",
	"CHANNEL_MESSAGE_GAME_SCORE":     "%[1]v scored %[3]v in game %[2]v",
	"CHANNEL_MESSAGE_GEO":            "%[1]v posted a location",
	"CHANNEL_MESSAGE_GEOLIVE":        "%[1]v posted a live location",
	"CHANNEL_MESSAGE_GIF":            "%[1]v posted a GIF",
	"CHANNEL_MESSAGE_GIVEAWAY":       "%[1]v posted a giveaway of %[2]vx %[3]vm Premium subscriptions",
	"CHANNEL_MESSAGE_GIVEAWAY_STARS": "%[1]v posted a giveaway of %[3]v stars %[2]v",
	"CHANNEL_MESSAGE_NOTEXT":         "%[1]v posted a message",
	"CHANNEL_MESSAGE_PAID_MEDIA":     "%[1]v posted a paid post for %[2]v star",
	"CHANNEL_MESSAGE_PHOTO":          "%[1]v posted a photo",
	"CHANNEL_MESSAGE_PHOTOS":         "%[1]v posted %[2]v photos",
	"CHANNEL_MESSAGE_PLAYLIST":       "%[1]v posted %[2]v music files",
	"CHANNEL_MESSAGE_POLL":           "%[1]v posted a poll %[2]v",
	"CHANNEL_MESSAGE_QUIZ":           "%[1]v posted a quiz %[2]v",
	"CHANNEL_MESSAGE_ROUND":          "%[1]v posted a video message",
	"CHANNEL_MESSAGE_STICKER":        "%[1]v posted a %[2]v sticker",
	"CHANNEL_MESSAGE_STORY":          "%[1]v shared a story",
	"CHANNEL_MESSAGE_TEXT":           "%[1]v: %[2]v",
	"CHANNEL_MESSAGE_VIDEO":          "%[1]v posted a video",
	"CHANNEL_MESSAGE_VIDEOS":         "%[1]v posted %[2]v videos",
	"CHAT_ADD_MEMBER":                "%[1]v invited %[3]v to the group %[2]v",
	"CHAT_ADD_YOU":                   "%[1]v invited you to the group %[2]v",
	"CHAT_CREATED":                   "%[1]v invited you to the group %[2]v",
	"CHAT_DELETE_MEMBER":             "%[1]v removed %[3]v from the group %[2]v",
	"CHAT_DELETE_YOU":                "%[1]v removed you from the group %[2]v",
	"CHAT_JOINED":                    "%[1]v joined the group %[2]v",
	"CHAT_LEFT":                      "%[1]v left the group %[2]v",
	"CHAT_MESSAGES":                  "%[1]v sent an album to the group %[2]v",
	"CHAT_MESSAGE_AUDIO":             "%[1]v sent a voice message to the group %[2]v",
	"CHAT_MESSAGE_CONTACT":           "%[1]v shared a contact %[3]v in the group %[2]v",
	"CHAT_MESSAGE_DOC":               "%[1]v sent a file to the group %[2]v",
	"CHAT_MESSAGE_DOCS":              "%[1]v sent %[3]v files to the group %[2]v",
	"CHAT_MESSAGE_FWDS":              "%[1]v forwarded %[3]v messages to the group %[2]v",
	"CHAT_MESSAGE_GAME":              "%[1]v invited the group %[2]v to play %[3]v",
	"CHAT_MESSAGE_GAME_SCORE":        "%[1]v scored %[4]v in game %[3]v in the group %[2]v",
	"CHAT_MESSAGE_GEO":               "%[1]v sent a location to the group %[2]v",
	"CHAT_MESSAGE_GEOLIVE":           "%[1]v shared a live location with the group %[2]v",
	"CHAT_MESSAGE_GIF":               "%[1]v sent a GIF to the group %[2]v",
	"CHAT_MESSAGE_GIVEAWAY":          "%[1]v sent a giveaway of %[3]vx %[4]vm Premium subscriptions to the group %[2]v",
	"CHAT_MESSAGE_GIVEAWAY_STARS":    "%[1]v sent a giveaway of %[4]v stars %[3]v to the group %[2]v",
	"CHAT_MESSAGE_INVOICE":           "%[1]v sent an invoice to the group %[2]v for %[3]v",
	"CHAT_MESSAGE_NOTEXT":            "%[1]v sent a message to the group %[2]v",
	"CHAT_MESSAGE_PAID_MEDIA":        "%[1]v posted a paid post in %[2]v group for %[3]v star",
	"CHAT_MESSAGE_PHOTO":             "%[1]v sent a photo to the group %[2]v",
	"CHAT_MESSAGE_PHOTOS":            "%[1]v sent %[3]v photos to the group %[2]v",
	"CHAT_MESSAGE_PLAYLIST":          "%[1]v sent %[3]v music files to the group %[2]v",
	"CHAT_MESSAGE_POLL":              "%[1]v sent a poll %[3]v to the group %[2]v",
	"CHAT_MESSAGE_QUIZ":              "%[1]v sent a quiz %[3]v to the group %[2]v",
	"CHAT_MESSAGE_ROUND":             "%[1]v sent a video message to the group %[2]v",
	"CHAT_MESSAGE_STICKER":           "%[1]v sent a %[3]v sticker to the group %[2]v",
	"CHAT_MESSAGE_STORY":             "%[1]v shared a story to the group",
	"CHAT_MESSAGE_TEXT":              "%[1]v @ %[2]v: %[3]v",
	"CHAT_MESSAGE_VIDEO":             "%[1]v sent a video to the group %[2]v",
	"CHAT_MESSAGE_VIDEOS":            "%[1]v sent %[3]v videos to the group %[2]v",
	"CHAT_PHOTO_EDITED":              "%[1]v changed the group photo for %[2]v",
	"CHAT_REACT_AUDIO":               "%[1]v: %[3]v to your voice message in %[2]v",
	"CHAT_REACT_CONTACT":             "%[1]v: %[3]v to your contact %[4]v in %[2]v",
	"CHAT_REACT_DOC":                 "%[1]v: %[3]v to your file in %[2]v",
	"CHAT_REACT_GAME":                "%[1]v: %[3]v to your game in %[2]v",
	"CHAT_REACT_GEO":                 "%[1]v: %[3]v to your map in %[2]v",
	"CHAT_REACT_GEOLIVE":             "%[1]v: %[3]v to your live location in %[2]v",
	"CHAT_REACT_GIF":                 "%[1]v: %[3]v to your GIF in %[2]v",
	"CHAT_REACT_GIVEAWAY":            "%[1]v reacted %[3]v in group %[2]v to your giveaway",
	"CHAT_REACT_INVOICE":             "%[1]v: %[3]v to your invoice in %[2]v",
	"CHAT_REACT_NOTEXT":              "%[1]v: %[3]v to your message in %[2]v",
	"CHAT_REACT_PAID_MEDIA":          "%[1]v reacted %[3]v in group %[2]v to your paid post for %[4]v star",
	"CHAT_REACT_PHOTO":               "%[1]v: %[3]v to your photo in %[2]v",
	"CHAT_REACT_POLL":                "%[1]v: %[3]v to your poll %[4]v in %[2]v",
	"CHAT_REACT_QUIZ":                "%[1]v: %[3]v to your quiz %[4]v in %[2]v",
	"CHAT_REACT_ROUND":               "%[1]v: %[3]v to your video message in %[2]v",
	"CHAT_REACT_STICKER":             "%[1]v: %[3]v to your %[4]v sticker in %[2]v",
	"CHAT_REACT_TEXT":                "%[1]v: %[3]v in %[2]v to your \"%[4]v\"",
	"CHAT_REACT_VIDEO":               "%[1]v: %[3]v to your video in %[2]v",
	"CHAT_REQ_JOINED":                "%[2]v|%[1]v was accepted into the group",
	"CHAT_RETURNED":                  "%[1]v returned to the group %[2]v",
	"CHAT_TITLE_EDITED":              "%[1]v renamed the group %[2]v",
	"CHAT_VOICECHAT_END":             "%[1]v ended a voice chat in the group %[2]v",
	"CHAT_VOICECHAT_INVITE":          "%[1]v invited %[3]v to a voice chat in the group %[2]v",
	"CHAT_VOICECHAT_INVITE_YOU":      "%[1]v invited you to a voice chat in the group %[2]v",
	"CHAT_VOICECHAT_START":           "%[1]v started a voice chat in the group %[2]v",
	"CONTACT_JOINED":                 "%[1]v joined Telegram!",
	"ENCRYPTED_MESSAGE":              "You have a new message",
	"ENCRYPTION_ACCEPT":              "You have a new message",
	"ENCRYPTION_REQUEST":             "You have a new message",
	"LOCKED_MESSAGE":                 "You have a new message",
	"MESSAGES":                       "%[1]v sent you an album",
	"MESSAGE_AUDIO":                  "%[1]v sent you a voice message",
	"MESSAGE_CONTACT":                "%[1]v shared a contact %[2]v with you",
	"MESSAGE_DOC":                    "%[1]v sent you a file",
	"MESSAGE_DOCS":                   "%[1]v sent you %[2]v files",
	"MESSAGE_FWDS":                   "%[1]v forwarded you %[2]v messages",
	"MESSAGE_GAME":                   "%[1]v invited you to play %[2]v",
	"MESSAGE_GAME_SCORE":             "%[1]v scored %[3]v in game %[2]v",
	"MESSAGE_GEO":                    "%[1]v sent you a location",
	"MESSAGE_GEOLIVE":                "%[1]v sent you a live location",
	"MESSAGE_GIF":                    "%[1]v sent you a GIF",
	"MESSAGE_GIFTCODE":               "%[1]v sent you a Gift Code for %[2]v of Telegram Premium",
	"MESSAGE_GIVEAWAY":               "%[1]v sent you a giveaway of %[2]vx %[3]vm Premium subscriptions",
	"MESSAGE_GIVEAWAY_STARS":         "%[1]v sent you a giveaway of %[3]v stars %[2]v",
	"MESSAGE_INVOICE":                "%[1]v sent you an invoice for %[2]v",
	"MESSAGE_NOTEXT":                 "%[1]v sent you a message",
	"MESSAGE_PAID_MEDIA":             "%[1]v sent you a paid post for %[2]v star",
	"MESSAGE_PHOTO":                  "%[1]v sent you a photo",
	"MESSAGE_PHOTOS":                 "%[1]v sent you %[2]v photos",
	"MESSAGE_PHOTO_SECRET":           "%[1]v sent you a self-destructing photo",
	"MESSAGE_PLAYLIST":               "%[1]v sent you %[2]v music files",
	"MESSAGE_POLL":                   "%[1]v sent you a poll %[2]v",
	"MESSAGE_QUIZ":                   "%[1]v sent you a quiz %[2]v",
	"MESSAGE_RECURRING_PAY":          "You were charged %[2]v by %[1]v",
	"MESSAGE_ROUND":                  "%[1]v sent you a video message",
	"MESSAGE_SAME_WALLPAPER":         "%[1]v set a same wallpaper for this chat",
	"MESSAGE_SCREENSHOT":             "%[1]v took a screenshot",
	"MESSAGE_STARGIFT":               "%[1]v sent you a Gift worth %[2]v Stars",
	"MESSAGE_STICKER":                "%[1]v sent you a %[2]v sticker",
	"MESSAGE_STORY":                  "%[1]v shared a story with you",
	"MESSAGE_STORY_MENTION":          "%[1]v mentioned you in a story",
	"MESSAGE_TEXT":                   "%[1]v: %[2]v",
	"MESSAGE_VIDEO":                  "%[1]v sent you a video",
	"MESSAGE_VIDEOS":                 "%[1]v sent you %[2]v videos",
	"MESSAGE_VIDEO_SECRET":           "%[1]v sent you a self-destructing video",
	"MESSAGE_WALLPAPER":              "%[1]v set a new wallpaper for this chat",
	"PHONE_CALL_MISSED":              "You missed a call from %[1]v",
	"PHONE_CALL_REQUEST":             "%[1]v is calling you!",
	"PINNED_AUDIO":                   "%[1]v pinned a voice message in the group %[2]v",
	"PINNED_CONTACT":                 "%[1]v pinned a contact %[3]v in the group %[2]v",
	"PINNED_DOC":                     "%[1]v pinned a file in the group %[2]v",
	"PINNED_GAME":                    "%[1]v pinned a game in the group %[2]v",
	"PINNED_GAME_SCORE":              "%[1]v pinned a game score in the group %[2]v",
	"PINNED_GEO":                     "%[1]v pinned a map in the group %[2]v",
	"PINNED_GEOLIVE":                 "%[1]v pinned a live location in the group %[2]v",
	"PINNED_GIF":                     "%[1]v pinned a GIF in the group %[2]v",
	"PINNED_GIVEAWAY":                "%[1]v pinned a giveaway in the group %[2]v",
	"PINNED_INVOICE":                 "%[1]v pinned an invoice in the group %[2]v",
	"PINNED_NOTEXT":                  "%[1]v pinned a message in the group %[2]v",
	"PINNED_PAID_MEDIA":              "%[1]v pinned a paid post for %[3]v star in the group %[2]v",
	"PINNED_PHOTO":                   "%[1]v pinned a photo in the group %[2]v",
	"PINNED_POLL":                    "%[1]v pinned a poll %[3]v in the group %[2]v",
	"PINNED_QUIZ":                    "%[1]v pinned a quiz %[3]v in the group %[2]v",
	"PINNED_ROUND":                   "%[1]v pinned a video message in the group %[2]v",
	"PINNED_STICKER":                 "%[1]v pinned a %[3]v sticker in the group %[2]v",
	"PINNED_TEXT":                    "%[1]v pinned \"%[3]v\" in the group %[2]v",
	"PINNED_VIDEO":                   "%[1]v pinned a video in the group %[2]v",
	"REACT_AUDIO":                    "%[1]v: %[2]v to your voice message",
	"REACT_CONTACT":                  "%[1]v: %[2]v to your contact %[3]v",
	"REACT_DOC":                      "%[1]v: %[2]v to your file",
	"REACT_GAME":                     "%[1]v: %[2]v to your game",
	"REACT_GEO":                      "%[1]v: %[2]v to your map",
	"REACT_GEOLIVE":                  "%[1]v: %[2]v to your live location",
	"REACT_GIF":                      "%[1]v: %[2]v to your GIF",
	"REACT_GIVEAWAY":                 "%[1]v reacted %[2]v to your giveaway",
	"REACT_HIDDEN":                   "New reaction to your message",
	"REACT_INVOICE":                  "%[1]v: %[2]v to your invoice",
	"REACT_NOTEXT":                   "%[1]v: %[2]v to your message",
	"REACT_PHOTO":                    "%[1]v: %[2]v to your photo",
	"REACT_POLL":                     "%[1]v: %[2]v to your poll %[3]v",
	"REACT_QUIZ":                     "%[1]v: %[2]v to your quiz %[3]v",
	"REACT_ROUND":                    "%[1]v: %[2]v to your video message",
	"REACT_STICKER":                  "%[1]v: %[2]v to your %[3]v sticker",
	"REACT_STORY":                    "%[1]v: %[2]v to your story",
	"REACT_STORY_HIDDEN":             "New reaction to your story",
	"REACT_TEXT":                     "%[1]v: %[2]v to your \"%[3]v\"",
	"REACT_VIDEO":                    "%[1]v: %[2]v to your video",
	"STORY_HIDDEN_AUTHOR":            "A new story was posted",
	"STORY_NOTEXT":                   "%[1]v posted a story",
}

var FullSyncOnConnectBackground = false

func (t *TelegramClient) ConnectBackground(ctx context.Context, params *bridgev2.ConnectBackgroundParams) error {
	data, _ := params.ExtraData.(*PushNotificationData)
	var relatedPortal *bridgev2.Portal
	var sender *bridgev2.Ghost
	var messageID networkid.MessageID
	var messageText, notificationText string
	if data != nil {
		tpl, ok := PushMessageFormats[data.LocKey]
		if ok {
			notificationText = fmt.Sprintf(tpl, exslices.CastToAny(data.LocArgs)...)
		}
		switch data.LocKey {
		case "MESSAGE_TEXT", "CHANNEL_MESSAGE_TEXT":
			messageText = data.LocArgs[1]
		case "CHAT_MESSAGE_TEXT":
			messageText = data.LocArgs[2]
		}
		var err error
		if data.Custom.ChannelID != 0 {
			relatedPortal, err = t.main.Bridge.GetPortalByKey(ctx, t.makePortalKeyFromID(ids.PeerTypeChannel, data.Custom.ChannelID))
		} else if data.Custom.ChatID != 0 {
			relatedPortal, err = t.main.Bridge.GetPortalByKey(ctx, t.makePortalKeyFromID(ids.PeerTypeChat, data.Custom.ChatID))
		} else if data.Custom.FromID != 0 {
			relatedPortal, err = t.main.Bridge.GetPortalByKey(ctx, t.makePortalKeyFromID(ids.PeerTypeUser, data.Custom.FromID))
		}
		if err != nil {
			return fmt.Errorf("failed to get related portal: %w", err)
		}
		if data.Custom.ChatFromBroadcastID != 0 {
			sender, err = t.main.Bridge.GetGhostByID(ctx, ids.MakeChannelUserID(data.Custom.FromID))
		} else if data.Custom.ChatFromGroupID != 0 {
			sender, err = t.main.Bridge.GetGhostByID(ctx, ids.MakeChannelUserID(data.Custom.ChatFromGroupID))
		} else if data.Custom.FromID != 0 {
			sender, err = t.main.Bridge.GetGhostByID(ctx, ids.MakeUserID(data.Custom.FromID))
		}
		if err != nil {
			return fmt.Errorf("failed to get sender: %w", err)
		}
		if relatedPortal != nil && data.Custom.MessageID != 0 {
			messageID = ids.MakeMessageID(relatedPortal.PortalKey, data.Custom.MessageID)
		}
	}
	notifs, ok := t.main.Bridge.Matrix.(bridgev2.MatrixConnectorWithNotifications)
	if ok {
		notifs.DisplayNotification(ctx, &bridgev2.DirectNotificationData{
			Portal:    relatedPortal,
			Sender:    sender,
			Message:   messageText,
			MessageID: messageID,

			FormattedNotification: notificationText,
		})
	}
	if FullSyncOnConnectBackground {
		t.Connect(ctx)
		defer t.Disconnect()
		// TODO is it possible to safely only sync one chat?
		select {
		case <-time.After(20 * time.Second):
		case <-ctx.Done():
		}
	}
	return nil
}

func (tg *TelegramConnector) ParsePushNotification(ctx context.Context, data json.RawMessage) (networkid.UserLoginID, any, error) {
	val := gjson.GetBytes(data, "p")
	if val.Type != gjson.String {
		return "", nil, fmt.Errorf("missing or invalid p field")
	}
	valBytes, err := base64.RawURLEncoding.DecodeString(val.Str)
	if err != nil {
		return "", nil, fmt.Errorf("failed to base64 decode p field: %w", err)
	}
	var em crypto.EncryptedMessage
	err = em.DecodeWithoutCopy(&bin.Buffer{Buf: valBytes})
	if err != nil {
		return "", nil, fmt.Errorf("failed to decode auth key and message ID: %w", err)
	}
	userIDs, err := tg.Bridge.DB.UserLogin.GetAllUserIDsWithLogins(ctx)
	if err != nil {
		return "", nil, fmt.Errorf("failed to get users with logins: %w", err)
	}
	var matchingAuthKey *crypto.AuthKey
	var userLoginID networkid.UserLoginID
UserLoop:
	for _, userID := range userIDs {
		user, err := tg.Bridge.GetExistingUserByMXID(ctx, userID)
		if err != nil {
			return "", nil, fmt.Errorf("failed to get user %s: %w", userID, err)
		}
		for _, login := range user.GetUserLogins() {
			key := login.Metadata.(*UserLoginMetadata).PushEncryptionKey
			if len(key) != 256 {
				continue
			}
			authKey := crypto.Key(key).WithID()
			if authKey.ID == em.AuthKeyID {
				matchingAuthKey = &authKey
				userLoginID = login.ID
				break UserLoop
			}
		}
	}
	if matchingAuthKey == nil {
		return "", nil, fmt.Errorf("no matching auth key found")
	}
	c := crypto.NewClientCipher(rand.Reader)
	plaintext, err := c.DecryptRaw(*matchingAuthKey, &em)
	if err != nil {
		return userLoginID, nil, fmt.Errorf("failed to decrypt payload: %w", err)
	}
	var pmd PushNotificationData
	err = json.Unmarshal(plaintext, &pmd)
	if err != nil {
		return userLoginID, nil, fmt.Errorf("failed to unmarshal decrypted payload: %w", err)
	}
	return userLoginID, &pmd, nil
}

func (t *TelegramClient) RegisterPushNotifications(ctx context.Context, pushType bridgev2.PushType, token string) error {
	meta := t.userLogin.Metadata.(*UserLoginMetadata)
	if meta.PushEncryptionKey == nil {
		meta.PushEncryptionKey = random.Bytes(256)
		err := t.userLogin.Save(ctx)
		if err != nil {
			return fmt.Errorf("failed to save push encryption key: %w", err)
		}
	}
	var tokenType int
	switch pushType {
	case bridgev2.PushTypeWeb:
		tokenType = 10
	case bridgev2.PushTypeFCM:
		tokenType = 2
	case bridgev2.PushTypeAPNs:
		tokenType = 1
	default:
		return fmt.Errorf("unsupported push type %s", pushType)
	}
	_, err := t.client.API().AccountRegisterDevice(ctx, &tg.AccountRegisterDeviceRequest{
		NoMuted:    false,
		TokenType:  tokenType,
		Token:      token,
		AppSandbox: PushAppSandbox,
		Secret:     meta.PushEncryptionKey,
		OtherUIDs:  nil, // TODO set properly
	})
	return err
}

func (t *TelegramClient) GetPushConfigs() *bridgev2.PushConfig {
	return &bridgev2.PushConfig{Native: true}
}
