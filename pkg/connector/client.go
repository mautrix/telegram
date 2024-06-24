package connector

import (
	"context"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/gotd/td/telegram"
	"github.com/gotd/td/telegram/updates"
	"github.com/gotd/td/tg"
	"github.com/rs/zerolog"
	"go.mau.fi/zerozap"
	"go.uber.org/zap"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/networkid"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/connector/msgconv"
	"go.mau.fi/mautrix-telegram/pkg/connector/util"
)

type TelegramClient struct {
	main         *TelegramConnector
	loginID      int64
	userLogin    *bridgev2.UserLogin
	client       *telegram.Client
	clientCancel context.CancelFunc
	msgConv      *msgconv.MessageConverter
}

var _ bridgev2.NetworkAPI = (*TelegramClient)(nil)

func NewTelegramClient(ctx context.Context, tc *TelegramConnector, login *bridgev2.UserLogin) (*TelegramClient, error) {
	loginID, err := strconv.ParseInt(string(login.ID), 10, 64)
	if err != nil {
		return nil, err
	}

	log := zerolog.Ctx(ctx).With().
		Str("component", "telegram_client").
		Int64("login_id", loginID).
		Logger()

	zaplog := zap.New(zerozap.New(log))

	client := TelegramClient{
		main:      tc,
		loginID:   loginID,
		userLogin: login,
	}
	dispatcher := tg.NewUpdateDispatcher()
	dispatcher.OnNewMessage(client.onUpdateNewMessage)
	dispatcher.OnNewChannelMessage(client.onUpdateNewChannelMessage)

	store := tc.store.GetScopedStore(loginID)

	updatesManager := updates.New(updates.Config{
		OnChannelTooLong: func(channelID int64) {
			log.Error().Int64("channel_id", channelID).Msg("OnChannelTooLong")
			panic("unimplemented channel too long")
		},
		Handler:      dispatcher,
		Logger:       zaplog.Named("gaps"),
		Storage:      store,
		AccessHasher: store,
	})

	client.client = telegram.NewClient(tc.Config.AppID, tc.Config.AppHash, telegram.Options{
		SessionStorage: store,
		Logger:         zaplog,
		UpdateHandler:  updatesManager,
	})
	client.msgConv = msgconv.NewMessageConverter(client.client, tc.Bridge.Matrix, tc.useDirectMedia)
	client.clientCancel, err = connectTelegramClient(ctx, client.client)
	go func() {
		err = updatesManager.Run(ctx, client.client.API(), loginID, updates.AuthOptions{})
		if err != nil {
			log.Err(err).Msg("updates manager error")
			client.clientCancel()
		}
	}()
	return &client, err
}

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
	switch msg := update.GetMessage().(type) {
	case *tg.Message:
		sender := t.getEventSender(msg)

		if media, ok := msg.GetMedia(); ok && media.TypeID() == tg.MessageMediaContactTypeID {
			contact := media.(*tg.MessageMediaContact)
			// TODO update the corresponding puppet
			log.Info().Int64("user_id", contact.UserID).Msg("received contact")
		}

		t.main.Bridge.QueueRemoteEvent(t.userLogin, &bridgev2.SimpleRemoteEvent[*tg.Message]{
			Type: bridgev2.RemoteEventMessage,
			LogContext: func(c zerolog.Context) zerolog.Context {
				return c.
					Int("message_id", update.Message.GetID()).
					Str("sender", string(sender.Sender)).
					Str("sender_login", string(sender.SenderLogin)).
					Bool("is_from_me", sender.IsFromMe)
			},
			ID:                 ids.MakeMessageID(msg.ID),
			Sender:             sender,
			PortalKey:          ids.MakePortalID(msg.PeerID),
			Data:               msg,
			CreatePortal:       true,
			ConvertMessageFunc: t.msgConv.ToMatrix,
			Timestamp:          time.Unix(int64(msg.Date), 0),
		})
	case *tg.MessageService:
		fmt.Printf("message service\n")
		fmt.Printf("%v\n", msg)

		// sender := t.getEventSender(msg)
		// switch action := msg.Action.(type) {
		// case *tg.MessageActionChatEditTitle:
		// case *tg.MessageActionChatCreate:
		// case *tg.MessageActionChatEditPhoto:
		// case *tg.MessageActionChatDeletePhoto:
		// case *tg.MessageActionChatAddUser:
		// case *tg.MessageActionChatDeleteUser:
		// case *tg.MessageActionChatJoinedByLink:
		// case *tg.MessageActionChannelCreate:
		// case *tg.MessageActionChatMigrateTo:
		// case *tg.MessageActionChannelMigrateFrom:
		// case *tg.MessageActionPinMessage:
		// case *tg.MessageActionHistoryClear:
		// case *tg.MessageActionGameScore:
		// case *tg.MessageActionPaymentSentMe:
		// case *tg.MessageActionPaymentSent:
		// case *tg.MessageActionPhoneCall:
		// case *tg.MessageActionScreenshotTaken:
		// case *tg.MessageActionCustomAction:
		// case *tg.MessageActionBotAllowed:
		// case *tg.MessageActionSecureValuesSentMe:
		// case *tg.MessageActionSecureValuesSent:
		// case *tg.MessageActionContactSignUp:
		// case *tg.MessageActionGeoProximityReached:
		// case *tg.MessageActionGroupCall:
		// case *tg.MessageActionInviteToGroupCall:
		// case *tg.MessageActionSetMessagesTTL:
		// case *tg.MessageActionGroupCallScheduled:
		// case *tg.MessageActionSetChatTheme:
		// case *tg.MessageActionChatJoinedByRequest:
		// case *tg.MessageActionWebViewDataSentMe:
		// case *tg.MessageActionWebViewDataSent:
		// case *tg.MessageActionGiftPremium:
		// case *tg.MessageActionTopicCreate:
		// case *tg.MessageActionTopicEdit:
		// case *tg.MessageActionSuggestProfilePhoto:
		// case *tg.MessageActionRequestedPeer:
		// case *tg.MessageActionSetChatWallPaper:
		// case *tg.MessageActionGiftCode:
		// case *tg.MessageActionGiveawayLaunch:
		// case *tg.MessageActionGiveawayResults:
		// case *tg.MessageActionBoostApply:
		// case *tg.MessageActionRequestedPeerSentMe:
		// default:
		// 	return fmt.Errorf("unknown action type %T", action)
		// }

	default:
		return fmt.Errorf("unknown message type %T", msg)
	}
	return nil
}

type messageWithSender interface {
	GetOut() bool
	GetFromID() (tg.PeerClass, bool)
	GetPeerID() tg.PeerClass
}

func (t *TelegramClient) getEventSender(msg messageWithSender) (sender bridgev2.EventSender) {
	if msg.GetOut() {
		sender.IsFromMe = true
		sender.SenderLogin = ids.MakeUserLoginID(t.loginID)
		sender.Sender = ids.MakeUserID(t.loginID)
	} else if f, ok := msg.GetFromID(); ok {
		switch from := f.(type) {
		case *tg.PeerUser:
			sender.SenderLogin = ids.MakeUserLoginID(from.UserID)
			sender.Sender = ids.MakeUserID(from.UserID)
		default:
			fmt.Printf("%+v\n", f)
			fmt.Printf("%T\n", f)
			panic("unimplemented FromID")
		}
	} else if peer, ok := msg.GetPeerID().(*tg.PeerUser); ok {
		sender.SenderLogin = ids.MakeUserLoginID(peer.UserID)
		sender.Sender = ids.MakeUserID(peer.UserID)
	} else {
		panic("not from anyone")
	}
	return
}

func (t *TelegramClient) onUpdateNewChannelMessage(ctx context.Context, e tg.Entities, update *tg.UpdateNewChannelMessage) error {
	fmt.Printf("update new channel message %+v\n", update)
	return nil
}

func (t *TelegramClient) Connect(ctx context.Context) (err error) {
	t.clientCancel, err = connectTelegramClient(ctx, t.client)
	return
}

func (t *TelegramClient) Disconnect() {
	t.clientCancel()
}

func (t *TelegramClient) GetChatInfo(ctx context.Context, portal *bridgev2.Portal) (*bridgev2.PortalInfo, error) {
	fmt.Printf("%+v\n", portal)
	peerType, id, err := ids.ParsePortalID(portal.ID)
	if err != nil {
		return nil, err
	}
	var name string
	var members []networkid.UserID
	var isSpace, isDM bool

	switch peerType {
	case ids.PeerTypeUser:
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
			name = util.FormatFullName(user.FirstName, user.LastName) // TODO gate this behind a config?
			members = []networkid.UserID{ids.MakeUserID(id), ids.MakeUserID(t.loginID)}
			isDM = true
		}
	case ids.PeerTypeChat:
		fullChat, err := t.client.API().MessagesGetFullChat(ctx, id)
		if err != nil {
			return nil, err
		}
		for _, c := range fullChat.Chats {
			if c.GetID() == id {
				name = c.(*tg.Chat).Title
				break
			}
		}

		for _, user := range fullChat.Users {
			members = append(members, ids.MakeUserID(user.GetID()))
		}
	default:
		fmt.Printf("%s %d\n", peerType, id)
		panic("unimplemented getchatinfo")
	}

	return &bridgev2.PortalInfo{
		Name: &name,
		// TODO
		// Avatar *Avatar

		Members:      members,
		IsDirectChat: &isDM,
		IsSpace:      &isSpace,
	}, nil
}

func (t *TelegramClient) GetUserInfo(ctx context.Context, ghost *bridgev2.Ghost) (*bridgev2.UserInfo, error) {
	id, err := ids.ParseUserID(ghost.ID)
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

		name := util.FormatFullName(user.FirstName, user.LastName)
		return &bridgev2.UserInfo{
			IsBot: &user.Bot,
			Name:  &name,
			// TODO
			// Avatar *Avatar
			Identifiers: identifiers,
		}, nil
	}
}

func (t *TelegramClient) IsLoggedIn() bool {
	_, err := t.client.Self(context.TODO())
	return err == nil
}

func (t *TelegramClient) LogoutRemote(ctx context.Context) {
	_, err := t.client.API().AuthLogOut(ctx)
	if err != nil {
		zerolog.Ctx(ctx).Err(err).Msg("failed to logout on Telegram")
	}
}

func (t *TelegramClient) IsThisUser(ctx context.Context, userID networkid.UserID) bool {
	return userID == networkid.UserID(t.userLogin.ID)
}

func (t *TelegramClient) GetCapabilities(ctx context.Context, portal *bridgev2.Portal) *bridgev2.NetworkRoomCapabilities {
	return &bridgev2.NetworkRoomCapabilities{
		FormattedText:    true,
		UserMentions:     true,
		RoomMentions:     true, // TODO?
		LocationMessages: true,
		Captions:         true,
		Threads:          false, // TODO
		Replies:          true,
		Edits:            true,
		Deletes:          true,
		ReadReceipts:     true,
		Reactions:        true,
	}
}
