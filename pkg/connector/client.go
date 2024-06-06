package connector

import (
	"context"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/gotd/td/telegram"
	"github.com/gotd/td/telegram/message"
	"github.com/gotd/td/telegram/updates"
	"github.com/gotd/td/telegram/updates/hook"
	"github.com/gotd/td/tg"
	"github.com/rs/zerolog"
	"go.mau.fi/zerozap"
	"go.uber.org/zap"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/event"
)

type TelegramClient struct {
	main         *TelegramConnector
	loginID      int64
	userLogin    *bridgev2.UserLogin
	client       *telegram.Client
	clientCancel context.CancelFunc
}

func NewTelegramClient(ctx context.Context, tc *TelegramConnector, login *bridgev2.UserLogin) (*TelegramClient, error) {
	loginID, err := strconv.ParseInt(string(login.ID), 10, 64)
	if err != nil {
		return nil, err
	}

	logger := zerolog.Ctx(ctx).With().
		Str("component", "telegram_client").
		Int64("login_id", loginID).
		Logger()

	zaplog := zap.New(zerozap.New(logger))

	client := TelegramClient{
		main:      tc,
		loginID:   loginID,
		userLogin: login,
	}

	dispatcher := tg.NewUpdateDispatcher()
	dispatcher.OnNewMessage(client.onUpdateNewMessage)

	updatesManager := updates.New(updates.Config{
		Handler: dispatcher,
		Logger:  zaplog.Named("gaps"),
	})

	client.client = telegram.NewClient(tc.Config.AppID, tc.Config.AppHash, telegram.Options{
		SessionStorage: tc.store.GetSessionStore(loginID),
		Logger:         zaplog,
		UpdateHandler:  updatesManager,
		Middlewares: []telegram.Middleware{
			hook.UpdateHook(updatesManager.Handle),
		},
	})
	client.clientCancel, err = connectTelegramClient(ctx, client.client)
	return &client, err
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

func (t *TelegramClient) onUpdateNewMessage(ctx context.Context, e tg.Entities, update *tg.UpdateNewMessage) error {
	log := zerolog.Ctx(ctx)
	msg, ok := update.GetMessage().(*tg.Message)
	if !ok {
		log.Error().Type("message", update.GetMessage()).Msg("unknown message type")
		return nil
	}

	var sender bridgev2.EventSender
	if msg.Out {
		sender.IsFromMe = true
		sender.SenderLogin = makeUserLoginID(t.loginID)
		sender.Sender = makeUserID(t.loginID)
	} else if msg.FromID != nil {
		switch from := msg.FromID.(type) {
		case *tg.PeerUser:
			sender.SenderLogin = makeUserLoginID(from.UserID)
			sender.Sender = makeUserID(from.UserID)
		default:
			fmt.Printf("%+v\n", msg.FromID)
			fmt.Printf("%T\n", msg.FromID)
			panic("unimplemented FromID")
		}
	} else if peer, ok := msg.PeerID.(*tg.PeerUser); ok {
		sender.SenderLogin = makeUserLoginID(peer.UserID)
		sender.Sender = makeUserID(peer.UserID)
	} else {
		panic("not from anyone")
	}

	t.main.Bridge.QueueRemoteEvent(t.userLogin, &bridgev2.SimpleRemoteEvent[*tg.Message]{
		Type: bridgev2.RemoteEventMessage,
		LogContext: func(c zerolog.Context) zerolog.Context {
			return c.
				Int("message_id", update.Message.GetID())
		},
		ID:           makeMessageID(msg.ID),
		Sender:       sender,
		PortalID:     makePortalID(msg.PeerID),
		Data:         msg,
		CreatePortal: true,

		ConvertMessageFunc: func(ctx context.Context, portal *bridgev2.Portal, intent bridgev2.MatrixAPI, data *tg.Message) (*bridgev2.ConvertedMessage, error) {
			cm := &bridgev2.ConvertedMessage{
				Timestamp: time.Unix(int64(data.Date), 0),
			}
			if data.Message != "" {
				converted := bridgev2.ConvertedMessagePart{
					Type:    event.EventMessage,
					Content: &event.MessageEventContent{MsgType: event.MsgText, Body: data.Message},
				}
				cm.Parts = append(cm.Parts, &converted)
			}
			return cm, nil
		},
	})
	return nil
}

func (t *TelegramClient) Connect(ctx context.Context) (err error) {
	t.clientCancel, err = connectTelegramClient(ctx, t.client)
	return
}

func getFullName(user *tg.User) string {
	return strings.TrimSpace(fmt.Sprintf("%s %s", user.FirstName, user.LastName))
}

func getFullNamePtr(user *tg.User) *string {
	fullName := getFullName(user)
	return &fullName
}

func (t *TelegramClient) GetChatInfo(ctx context.Context, portal *bridgev2.Portal) (*bridgev2.PortalInfo, error) {
	fmt.Printf("%+v\n", portal)
	peerType, id, err := parsePortalID(portal.ID)
	if err != nil {
		return nil, err
	}
	isSpace := false

	switch peerType {
	case peerTypeUser:
		users, err := t.client.API().UsersGetUsers(ctx, []tg.InputUserClass{&tg.InputUser{UserID: id}})
		if err != nil {
			return nil, err
		}
		if len(users) == 0 {
			return nil, fmt.Errorf("failed to get user info for user %d", id)
		}
		if user, ok := users[0].(*tg.User); !ok {
			return nil, fmt.Errorf("returned user is not *tg.User")
		} else {
			isDM := true
			return &bridgev2.PortalInfo{
				Name: getFullNamePtr(user),
				// Topic  *string
				// Avatar *Avatar

				Members:      []networkid.UserID{makeUserID(id), makeUserID(t.loginID)},
				IsDirectChat: &isDM,
				IsSpace:      &isSpace,
			}, nil
		}
	}
	fmt.Printf("%s %d\n", peerType, id)
	panic("unimplemented getchatinfo")
}

func (t *TelegramClient) GetUserInfo(ctx context.Context, ghost *bridgev2.Ghost) (*bridgev2.UserInfo, error) {
	id, err := parseUserID(ghost.ID)
	if err != nil {
		return nil, err
	}
	users, err := t.client.API().UsersGetUsers(ctx, []tg.InputUserClass{&tg.InputUser{UserID: id}})
	if err != nil {
		return nil, err
	}
	if len(users) == 0 {
		return nil, fmt.Errorf("failed to get user info for user %d", id)
	}
	if user, ok := users[0].(*tg.User); !ok {
		return nil, fmt.Errorf("returned user is not *tg.User")
	} else {
		var identifiers []string

		if username, ok := user.GetUsername(); ok {
			identifiers = append(identifiers, fmt.Sprintf("telegram:%s", username))
		}
		if phone, ok := user.GetPhone(); ok {
			identifiers = append(identifiers, fmt.Sprintf("tel:+%s", strings.TrimPrefix(phone, "+")))
		}

		return &bridgev2.UserInfo{
			IsBot: &user.Bot,
			Name:  getFullNamePtr(user),
			// Avatar *Avatar
			Identifiers: identifiers,
		}, nil
	}
}

func (t *TelegramClient) HandleMatrixEdit(ctx context.Context, msg *bridgev2.MatrixEdit) error {
	panic("unimplemented edit")
}

func (t *TelegramClient) HandleMatrixMessage(ctx context.Context, msg *bridgev2.MatrixMessage) (dbMessage *database.Message, err error) {
	sender := message.NewSender(t.client.API())
	peer, err := inputPeerForPortalID(msg.Portal.ID)
	if err != nil {
		return nil, err
	}

	updates, err := sender.To(peer).Text(ctx, msg.Content.Body)
	if err != nil {
		return nil, err
	}
	sentMessage, ok := updates.(*tg.UpdateShortSentMessage)
	if !ok {
		return nil, fmt.Errorf("unknown update from message response %T", updates)
	}

	dbMessage = &database.Message{
		ID:        makeMessageID(sentMessage.ID),
		MXID:      msg.Event.ID,
		RoomID:    msg.Portal.ID,
		SenderID:  makeUserID(t.loginID),
		Timestamp: time.Unix(int64(sentMessage.Date), 0),
	}
	return
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
	return userID == networkid.UserID(t.userLogin.ID)
}

func (t *TelegramClient) LogoutRemote(ctx context.Context) {
	_, err := t.client.API().AuthLogOut(ctx)
	if err != nil {
		zerolog.Ctx(ctx).Err(err).Msg("failed to logout on Telegram")
	}
}
