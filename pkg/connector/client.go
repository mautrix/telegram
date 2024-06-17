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
	"github.com/gotd/td/telegram/message/html"
	"github.com/gotd/td/telegram/updates"
	"github.com/gotd/td/telegram/uploader"
	"github.com/gotd/td/tg"
	"github.com/rs/zerolog"
	"go.mau.fi/zerozap"
	"go.uber.org/zap"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/event"

	"go.mau.fi/mautrix-telegram/pkg/connector/msgconv"
)

type TelegramClient struct {
	main         *TelegramConnector
	loginID      int64
	userLogin    *bridgev2.UserLogin
	client       *telegram.Client
	clientCancel context.CancelFunc
	msgConv      *msgconv.MessageConverter
}

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
	client.msgConv = msgconv.NewMessageConverter(client.client)
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
				Int("message_id", update.Message.GetID()).
				Str("sender", string(sender.Sender)).
				Str("sender_login", string(sender.SenderLogin)).
				Bool("is_from_me", sender.IsFromMe)
		},
		ID:                 makeMessageID(msg.ID),
		Sender:             sender,
		PortalID:           makePortalID(msg.PeerID),
		Data:               msg,
		CreatePortal:       true,
		ConvertMessageFunc: t.msgConv.ToMatrix,
	})
	return nil
}

func (t *TelegramClient) onUpdateNewChannelMessage(ctx context.Context, e tg.Entities, update *tg.UpdateNewChannelMessage) error {
	fmt.Printf("update new channel message %+v\n", update)
	return nil
}

func (t *TelegramClient) Connect(ctx context.Context) (err error) {
	t.clientCancel, err = connectTelegramClient(ctx, t.client)
	return
}

func getFullName(user *tg.User) string {
	return strings.TrimSpace(fmt.Sprintf("%s %s", user.FirstName, user.LastName))
}

func (t *TelegramClient) GetChatInfo(ctx context.Context, portal *bridgev2.Portal) (*bridgev2.PortalInfo, error) {
	fmt.Printf("%+v\n", portal)
	peerType, id, err := parsePortalID(portal.ID)
	if err != nil {
		return nil, err
	}
	var name, topic string
	var members []networkid.UserID
	var isSpace, isDM bool

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
			name = getFullName(user) // TODO gate this behind a config?
			members = []networkid.UserID{makeUserID(id), makeUserID(t.loginID)}
			isDM = true
		}
	case peerTypeChat:
		// TODO get name of chat
		chat, err := t.client.API().MessagesGetFullChat(ctx, id)
		if err != nil {
			return nil, err
		}
		if len(chat.Users) == 0 {
			return nil, fmt.Errorf("no users found in chat %d", id)
		}
		for _, user := range chat.Users {
			members = append(members, makeUserID(user.GetID()))
		}
	default:
		fmt.Printf("%s %d\n", peerType, id)
		panic("unimplemented getchatinfo")
	}

	return &bridgev2.PortalInfo{
		Name:  &name,
		Topic: &topic, // TODO
		// TODO
		// Avatar *Avatar

		Members:      members,
		IsDirectChat: &isDM,
		IsSpace:      &isSpace,
	}, nil
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

		name := getFullName(user)
		return &bridgev2.UserInfo{
			IsBot: &user.Bot,
			Name:  &name,
			// TODO
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
	builder := sender.To(peer)

	// TODO handle sticker

	var updates tg.UpdatesClass
	switch msg.Content.MsgType {
	case event.MsgText:
		updates, err = builder.Text(ctx, msg.Content.Body)
		if err != nil {
			return nil, err
		}
	case event.MsgImage, event.MsgFile, event.MsgAudio, event.MsgVideo:
		var filename, caption string
		if msg.Content.FileName != "" {
			filename = msg.Content.FileName
			caption = msg.Content.FormattedBody
			if caption == "" {
				caption = msg.Content.Body
			}
		} else {
			filename = msg.Content.Body
		}

		// TODO stream this download straight into the uploader
		fileData, err := t.main.Bridge.Bot.DownloadMedia(ctx, msg.Content.URL, msg.Content.File)
		if err != nil {
			return nil, fmt.Errorf("failed to download media from Matrix: %w", err)
		}
		uploader := uploader.NewUploader(t.client.API())
		upload, err := uploader.FromBytes(ctx, filename, fileData)
		if err != nil {
			return nil, fmt.Errorf("failed to upload media to Telegram: %w", err)
		}
		var photo *message.UploadedPhotoBuilder
		if caption != "" {
			// TODO resolver?
			photo = message.UploadedPhoto(upload, html.String(nil, caption))
		} else {
			photo = message.UploadedPhoto(upload)
		}
		updates, err = builder.Media(ctx, photo)
		if err != nil {
			return nil, err
		}
	}

	var tgMessageID, tgDate int
	switch sentMessage := updates.(type) {
	case *tg.UpdateShortSentMessage:
		tgMessageID = sentMessage.ID
		tgDate = sentMessage.Date
	case *tg.Updates:
		tgDate = sentMessage.Date
		for _, u := range sentMessage.Updates {
			if update, ok := u.(*tg.UpdateMessageID); ok {
				tgMessageID = update.ID
				break
			}
		}
		if tgMessageID == 0 {
			return nil, fmt.Errorf("couldn't find update message ID update")
		}
	default:
		return nil, fmt.Errorf("unknown update from message response %T", updates)
	}

	dbMessage = &database.Message{
		ID:        makeMessageID(tgMessageID),
		MXID:      msg.Event.ID,
		RoomID:    msg.Portal.ID,
		SenderID:  makeUserID(t.loginID),
		Timestamp: time.Unix(int64(tgDate), 0),
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
