package connector

import (
	"context"
	"errors"

	"github.com/gotd/td/telegram"
	"github.com/rs/zerolog"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/networkid"
)

type TelegramClient struct {
	main         *TelegramConnector
	userLogin    *bridgev2.UserLogin
	client       *telegram.Client
	clientCancel context.CancelFunc
}

var _ bridgev2.NetworkAPI = (*TelegramClient)(nil)

// connectTelegramClient blocks until client is connected, calling Run
// internally.
// Technique from: https://github.com/gotd/contrib/blob/master/bg/connect.go
func connectTelegramClient(ctx context.Context, client *telegram.Client) (context.CancelFunc, error) {
	ctx, cancel := context.WithCancel(ctx)

	errC := make(chan error, 1)
	initDone := make(chan struct{})
	go func() {
		defer close(errC)
		errC <- client.Run(ctx, func(ctx context.Context) error {
			close(initDone)
			<-ctx.Done()
			if errors.Is(ctx.Err(), context.Canceled) {
				return nil
			}
			return ctx.Err()
		})
	}()

	select {
	case <-ctx.Done(): // context canceled
		cancel()
		return func() {}, ctx.Err()
	case err := <-errC: // startup timeout
		cancel()
		return func() {}, err
	case <-initDone: // init done
	}

	return cancel, nil
}

func (t *TelegramClient) Connect(ctx context.Context) (err error) {
	t.clientCancel, err = connectTelegramClient(ctx, t.client)
	return
}

func (t *TelegramClient) GetChatInfo(ctx context.Context, portal *bridgev2.Portal) (*bridgev2.PortalInfo, error) {
	panic("unimplemented getchatinfo")
}

func (t *TelegramClient) GetUserInfo(ctx context.Context, ghost *bridgev2.Ghost) (*bridgev2.UserInfo, error) {
	panic("unimplemented getuserinfo")
}

func (t *TelegramClient) HandleMatrixEdit(ctx context.Context, msg *bridgev2.MatrixEdit) error {
	panic("unimplemented edit")
}

func (t *TelegramClient) HandleMatrixMessage(ctx context.Context, msg *bridgev2.MatrixMessage) (message *database.Message, err error) {
	panic("unimplemented message")
}

func (t *TelegramClient) HandleMatrixMessageRemove(ctx context.Context, msg *bridgev2.MatrixMessageRemove) error {
	panic("unimplemented remove")
}

func (t *TelegramClient) HandleMatrixReaction(ctx context.Context, msg *bridgev2.MatrixReaction) (emojiID networkid.EmojiID, err error) {
	panic("unimplemented reaction")
}

func (t *TelegramClient) HandleMatrixReactionRemove(ctx context.Context, msg *bridgev2.MatrixReactionRemove) error {
	panic("unimplemented reaction remove")
}

func (t *TelegramClient) IsLoggedIn() bool {
	_, err := t.client.Self(context.TODO())
	return err == nil
}

func (t *TelegramClient) IsThisUser(ctx context.Context, userID networkid.UserID) bool {
	panic("unimplemented istheiruser")
}

func (t *TelegramClient) LogoutRemote(ctx context.Context) {
	_, err := t.client.API().AuthLogOut(ctx)
	if err != nil {
		zerolog.Ctx(ctx).Err(err).Msg("failed to logout on Telegram")
	}
}
