package connector

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"fmt"

	"github.com/gotd/td/bin"
	"github.com/gotd/td/crypto"
	"github.com/gotd/td/tg"
	"github.com/tidwall/gjson"
	"go.mau.fi/util/random"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/networkid"
)

var (
	_ bridgev2.PushableNetworkAPI = (*TelegramClient)(nil)
	_ bridgev2.PushParsingNetwork = (*TelegramConnector)(nil)
)

var PushAppSandbox = false

// TODO
type PushNotificationData map[string]any

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
	pmd := make(PushNotificationData)
	err = json.Unmarshal(plaintext, &pmd)
	if err != nil {
		return userLoginID, nil, fmt.Errorf("failed to unmarshal decrypted payload: %w", err)
	}
	return userLoginID, pmd, nil
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
