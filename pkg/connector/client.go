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

	"go.mau.fi/mautrix-telegram/pkg/connector/download"
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

var (
	_ bridgev2.NetworkAPI                    = (*TelegramClient)(nil)
	_ bridgev2.EditHandlingNetworkAPI        = (*TelegramClient)(nil)
	_ bridgev2.ReactionHandlingNetworkAPI    = (*TelegramClient)(nil)
	_ bridgev2.RedactionHandlingNetworkAPI   = (*TelegramClient)(nil)
	_ bridgev2.ReadReceiptHandlingNetworkAPI = (*TelegramClient)(nil)
	_ bridgev2.ReadReceiptHandlingNetworkAPI = (*TelegramClient)(nil)
	_ bridgev2.TypingHandlingNetworkAPI      = (*TelegramClient)(nil)
	// _ bridgev2.IdentifierResolvingNetworkAPI = (*TelegramClient)(nil)
	// _ bridgev2.GroupCreatingNetworkAPI       = (*TelegramClient)(nil)
	// _ bridgev2.ContactListingNetworkAPI      = (*TelegramClient)(nil)
)

type UpdateDispatcher struct {
	tg.UpdateDispatcher
	EntityHandler func(context.Context, tg.Entities) error
}

func (u UpdateDispatcher) Handle(ctx context.Context, updates tg.UpdatesClass) error {
	var e tg.Entities
	switch u := updates.(type) {
	case *tg.Updates:
		e.Users = u.MapUsers().NotEmptyToMap()
		chats := u.MapChats()
		e.Chats = chats.ChatToMap()
		e.Channels = chats.ChannelToMap()
	case *tg.UpdatesCombined:
		e.Users = u.MapUsers().NotEmptyToMap()
		chats := u.MapChats()
		e.Chats = chats.ChatToMap()
		e.Channels = chats.ChannelToMap()
	}
	if u.EntityHandler != nil {
		u.EntityHandler(ctx, e)
	}

	return u.UpdateDispatcher.Handle(ctx, updates)
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
	dispatcher := UpdateDispatcher{
		UpdateDispatcher: tg.NewUpdateDispatcher(),
		EntityHandler:    client.onEntityUpdate,
	}
	dispatcher.OnNewMessage(client.onUpdateNewMessage)
	dispatcher.OnNewChannelMessage(client.onUpdateNewChannelMessage)
	dispatcher.OnUserName(client.onUserName)
	dispatcher.OnDeleteMessages(client.onDeleteMessages)

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

func (t *TelegramClient) onUserName(ctx context.Context, e tg.Entities, update *tg.UpdateUserName) error {
	ghost, err := t.main.Bridge.GetGhostByID(ctx, ids.MakeUserID(update.UserID))
	if err != nil {
		return err
	}

	name := util.FormatFullName(update.FirstName, update.LastName)

	// TODO update identifiers?
	ghost.UpdateInfo(ctx, &bridgev2.UserInfo{Name: &name})
	return nil
}

func (t *TelegramClient) onDeleteMessages(ctx context.Context, e tg.Entities, update *tg.UpdateDeleteMessages) error {
	for _, messageID := range update.Messages {
		parts, err := t.main.Bridge.DB.Message.GetAllPartsByID(ctx, ids.MakeMessageID(messageID))
		if err != nil {
			return err
		}
		if len(parts) == 0 {
			return fmt.Errorf("no parts found for message %d", messageID)
		}
		t.main.Bridge.QueueRemoteEvent(t.userLogin, &bridgev2.SimpleRemoteEvent[any]{
			Type: bridgev2.RemoteEventMessageRemove,
			LogContext: func(c zerolog.Context) zerolog.Context {
				return c.
					Str("action", "delete message").
					Int("message_id", messageID)
			},
			PortalKey:     parts[0].Room,
			CreatePortal:  false,
			TargetMessage: ids.MakeMessageID(messageID),
		})
	}
	return nil
}

func (t *TelegramClient) onEntityUpdate(ctx context.Context, e tg.Entities) error {
	for userID, user := range e.Users {
		ghost, err := t.main.Bridge.GetGhostByID(ctx, ids.MakeUserID(userID))
		if err != nil {
			return err
		}
		userInfo, err := t.getUserInfoFromTelegramUser(ctx, user)
		if err != nil {
			return err
		}
		ghost.UpdateInfo(ctx, userInfo)
	}
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
	var avatar *bridgev2.Avatar

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

		chatFull, ok := fullChat.FullChat.(*tg.ChatFull)
		if !ok {
			return nil, fmt.Errorf("full chat is not %T", chatFull)
		}

		if photo, ok := chatFull.ChatPhoto.(*tg.Photo); ok {
			avatar = &bridgev2.Avatar{
				ID: ids.MakeAvatarID(photo.ID),
				Get: func(ctx context.Context) (data []byte, err error) {
					data, _, _, _, err = download.DownloadPhoto(ctx, t.client.API(), photo)
					return
				},
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
		Name:         &name,
		Avatar:       avatar,
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
		return t.getUserInfoFromTelegramUser(ctx, user)
	}
}

func (t *TelegramClient) getUserInfoFromTelegramUser(ctx context.Context, user *tg.User) (*bridgev2.UserInfo, error) {
	var identifiers []string
	for _, username := range user.Usernames {
		identifiers = append(identifiers, fmt.Sprintf("telegram:%s", username.Username))
	}
	if phone, ok := user.GetPhone(); ok {
		identifiers = append(identifiers, fmt.Sprintf("tel:+%s", strings.TrimPrefix(phone, "+")))
	}

	var avatar *bridgev2.Avatar
	if p, ok := user.GetPhoto(); ok && p.TypeID() == tg.UserProfilePhotoTypeID {
		photo := p.(*tg.UserProfilePhoto)
		avatar = &bridgev2.Avatar{
			ID: ids.MakeAvatarID(photo.PhotoID),
			Get: func(ctx context.Context) (data []byte, err error) {
				data, _, err = download.DownloadPhotoFileLocation(ctx, t.client.API(), &tg.InputPeerPhotoFileLocation{
					Peer:    &tg.InputPeerUser{UserID: user.ID},
					PhotoID: photo.PhotoID,
					Big:     true,
				})
				return
			},
		}
	}

	name := util.FormatFullName(user.FirstName, user.LastName)
	return &bridgev2.UserInfo{
		IsBot:       &user.Bot,
		Name:        &name,
		Avatar:      avatar,
		Identifiers: identifiers,
	}, nil
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
