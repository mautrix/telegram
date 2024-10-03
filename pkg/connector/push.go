package connector

import (
	"context"
	"fmt"

	"github.com/gotd/td/tg"
	"maunium.net/go/mautrix/bridgev2"
)

var _ bridgev2.PushableNetworkAPI = (*TelegramClient)(nil)

func (t *TelegramClient) RegisterPushNotifications(ctx context.Context, pushType bridgev2.PushType, token string) error {
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
		AppSandbox: false,
		Secret:     nil,
		OtherUIDs:  nil, // TODO set properly
	})
	return err
}

func (t *TelegramClient) GetPushConfigs() *bridgev2.PushConfig {
	return &bridgev2.PushConfig{}
}
