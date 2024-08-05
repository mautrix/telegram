package connector

import (
	"context"
	"errors"
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/gotd/td/telegram"
	"github.com/gotd/td/telegram/updates"
	"github.com/gotd/td/tg"
	"github.com/rs/zerolog"
	"go.mau.fi/zerozap"
	"go.uber.org/zap"
	"maunium.net/go/mautrix/bridge/status"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/id"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/connector/matrixfmt"
	"go.mau.fi/mautrix-telegram/pkg/connector/media"
	"go.mau.fi/mautrix-telegram/pkg/connector/store"
	"go.mau.fi/mautrix-telegram/pkg/connector/telegramfmt"
	"go.mau.fi/mautrix-telegram/pkg/connector/util"
)

type TelegramClient struct {
	main           *TelegramConnector
	ScopedStore    *store.ScopedStore
	telegramUserID int64
	loginID        networkid.UserLoginID
	userID         networkid.UserID
	userLogin      *bridgev2.UserLogin
	client         *telegram.Client
	clientCancel   context.CancelFunc

	appConfig     map[string]any
	appConfigHash int

	telegramFmtParams *telegramfmt.FormatParams
	matrixParser      *matrixfmt.HTMLParser
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

var messageLinkRegex = regexp.MustCompile(`^https?:\/\/t(?:elegram)?\.(?:me|dog)\/([A-Za-z][A-Za-z0-9_]{3,31}[A-Za-z0-9]|[Cc]\/[0-9]{1,20})\/([0-9]{1,20})$`)

func NewTelegramClient(ctx context.Context, tc *TelegramConnector, login *bridgev2.UserLogin) (*TelegramClient, error) {
	telegramUserID, err := ids.ParseUserLoginID(login.ID)
	if err != nil {
		return nil, err
	}

	log := zerolog.Ctx(ctx).With().
		Str("component", "telegram_client").
		Str("user_login_id", string(login.ID)).
		Logger()

	zaplog := zap.New(zerozap.New(log))

	client := TelegramClient{
		main:           tc,
		telegramUserID: telegramUserID,
		loginID:        login.ID,
		userID:         networkid.UserID(login.ID),
		userLogin:      login,
	}
	dispatcher := UpdateDispatcher{
		UpdateDispatcher: tg.NewUpdateDispatcher(),
		EntityHandler:    client.onEntityUpdate,
	}
	dispatcher.OnNewMessage(func(ctx context.Context, e tg.Entities, update *tg.UpdateNewMessage) error {
		return client.onUpdateNewMessage(ctx, update)
	})
	dispatcher.OnNewChannelMessage(func(ctx context.Context, e tg.Entities, update *tg.UpdateNewChannelMessage) error {
		return client.onUpdateNewMessage(ctx, update)
	})
	dispatcher.OnUserName(client.onUserName)
	dispatcher.OnDeleteMessages(func(ctx context.Context, e tg.Entities, update *tg.UpdateDeleteMessages) error {
		return client.onDeleteMessages(ctx, update)
	})
	dispatcher.OnDeleteChannelMessages(func(ctx context.Context, e tg.Entities, update *tg.UpdateDeleteChannelMessages) error {
		return client.onDeleteMessages(ctx, update)
	})
	dispatcher.OnEditMessage(func(ctx context.Context, e tg.Entities, update *tg.UpdateEditMessage) error {
		return client.onMessageEdit(ctx, update)
	})
	dispatcher.OnEditChannelMessage(func(ctx context.Context, e tg.Entities, update *tg.UpdateEditChannelMessage) error {
		return client.onMessageEdit(ctx, update)
	})

	client.ScopedStore = tc.Store.GetScopedStore(telegramUserID)

	updatesManager := updates.New(updates.Config{
		OnChannelTooLong: func(channelID int64) {
			log.Warn().Int64("channel_id", channelID).Msg("channel too long")
		},
		Handler:      dispatcher,
		Logger:       zaplog.Named("gaps"),
		Storage:      client.ScopedStore,
		AccessHasher: client.ScopedStore,
	})

	client.client = telegram.NewClient(tc.Config.AppID, tc.Config.AppHash, telegram.Options{
		SessionStorage: client.ScopedStore,
		Logger:         zaplog,
		UpdateHandler:  updatesManager,
	})
	client.clientCancel, err = connectTelegramClient(ctx, client.client)

	client.telegramFmtParams = &telegramfmt.FormatParams{
		GetUserInfoByID: func(ctx context.Context, id int64) (telegramfmt.UserInfo, error) {
			ghost, err := tc.Bridge.GetGhostByID(ctx, ids.MakeUserID(id))
			if err != nil {
				return telegramfmt.UserInfo{}, err
			}
			userInfo := telegramfmt.UserInfo{MXID: ghost.Intent.GetMXID(), Name: ghost.Name}
			if id == client.telegramUserID {
				userInfo.MXID = client.userLogin.UserMXID
			}
			return userInfo, nil
		},
		GetUserInfoByUsername: func(ctx context.Context, username string) (telegramfmt.UserInfo, error) {
			ghosts, err := tc.Bridge.DB.Ghost.GetByMetadata(ctx, "username", username)
			if err != nil {
				return telegramfmt.UserInfo{}, err
			}
			if len(ghosts) != 1 {
				return telegramfmt.UserInfo{}, fmt.Errorf("username %s not found", username)
			}
			ghost, err := tc.Bridge.GetGhostByID(ctx, ghosts[0].ID)
			if err != nil {
				return telegramfmt.UserInfo{}, err
			}
			userInfo := telegramfmt.UserInfo{MXID: ghost.Intent.GetMXID(), Name: ghost.Name}
			if ghosts[0].ID == client.userID {
				userInfo.MXID = client.userLogin.UserMXID
			}
			return userInfo, nil
		},
		NormalizeURL: func(ctx context.Context, url string) string {
			log := zerolog.Ctx(ctx).With().
				Str("conversion_direction", "to_matrix").
				Str("entity_type", "url").
				Logger()

			if !strings.HasPrefix(url, "https://") && !strings.HasPrefix(url, "http://") && !strings.HasPrefix(url, "ftp://") && !strings.HasPrefix(url, "magnet://") {
				url = "http://" + url
			}

			submatches := messageLinkRegex.FindStringSubmatch(url)
			if len(submatches) == 0 {
				return url
			}
			group := submatches[1]
			msgID, err := strconv.Atoi(submatches[2])
			if err != nil {
				log.Err(err).Msg("error parsing message ID")
				return url
			}

			var portalKey networkid.PortalKey
			if strings.HasPrefix(group, "C/") || strings.HasPrefix(group, "c/") {
				portalKey = networkid.PortalKey{ID: networkid.PortalID(fmt.Sprintf("%s:%s", ids.PeerTypeChannel, group[2:]))}
			} else {
				portalKey = networkid.PortalKey{ID: networkid.PortalID(fmt.Sprintf("%s:%s", ids.PeerTypeUser, group))}
			}

			portal, err := tc.Bridge.DB.Portal.GetByKey(ctx, portalKey)
			if err != nil {
				log.Err(err).Msg("error getting portal")
				return url
			}

			message, err := tc.Bridge.DB.Message.GetFirstPartByID(ctx, client.loginID, ids.MakeMessageID(msgID))
			if err != nil {
				log.Err(err).Msg("error getting message")
				return url
			}

			return fmt.Sprintf("https://matrix.to/#/%s/%s", portal.MXID, message.MXID)
		},
	}
	client.matrixParser = &matrixfmt.HTMLParser{
		GetGhostDetails: func(ctx context.Context, ui id.UserID) (networkid.UserID, string, int64, bool) {
			if userID, ok := tc.Bridge.Matrix.ParseGhostMXID(ui); !ok {
				return "", "", 0, false
			} else if ghost, err := tc.Bridge.GetGhostByID(ctx, userID); err != nil {
				return "", "", 0, false
			} else {
				return userID, ghost.Metadata.(*GhostMetadata).Username, ghost.Metadata.(*GhostMetadata).AccessHash, true
			}
		},
	}

	go func() {
		err = updatesManager.Run(ctx, client.client.API(), telegramUserID, updates.AuthOptions{})
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

func (t *TelegramClient) Connect(ctx context.Context) (err error) {
	t.clientCancel, err = connectTelegramClient(ctx, t.client)
	t.userLogin.BridgeState.Send(status.BridgeState{StateEvent: status.StateConnected})
	return
}

func (t *TelegramClient) Disconnect() {
	t.clientCancel()
}

func (t *TelegramClient) updateUsersFromResponse(ctx context.Context, resp interface{ GetUsers() []tg.UserClass }) error {
	// TODO table for the access hashes?
	for _, user := range resp.GetUsers() {
		user, ok := user.(*tg.User)
		if !ok {
			return fmt.Errorf("user is %T not *tg.User", user)
		}
		err := t.updateGhost(ctx, user.ID, user)
		if err != nil {
			return err
		}
	}
	return nil
}

func (t *TelegramClient) GetUserInfo(ctx context.Context, ghost *bridgev2.Ghost) (*bridgev2.UserInfo, error) {
	id, err := ids.ParseUserID(ghost.ID)
	if err != nil {
		return nil, err
	}
	users, err := t.client.API().UsersGetUsers(ctx, []tg.InputUserClass{&tg.InputUser{
		UserID:     id,
		AccessHash: ghost.Metadata.(*GhostMetadata).AccessHash,
	}})
	if err != nil {
		return nil, err
	}
	if len(users) == 0 {
		return nil, fmt.Errorf("failed to get user info for user %d", id)
	}
	userInfo, err := t.getUserInfoFromTelegramUser(users[0])
	if err != nil {
		return nil, err
	}
	return userInfo, t.updateGhostWithUserInfo(ctx, id, userInfo)
}

func (t *TelegramClient) getUserInfoFromTelegramUser(u tg.UserClass) (*bridgev2.UserInfo, error) {
	user, ok := u.(*tg.User)
	if !ok {
		return nil, fmt.Errorf("user is %T not *tg.User", user)
	}
	var identifiers []string
	if !user.Min {
		if username, ok := user.GetUsername(); ok {
			identifiers = append(identifiers, fmt.Sprintf("telegram:%s", username))
		}
		for _, username := range user.Usernames {
			identifiers = append(identifiers, fmt.Sprintf("telegram:%s", username.Username))
		}
		if phone, ok := user.GetPhone(); ok {
			identifiers = append(identifiers, fmt.Sprintf("tel:+%s", strings.TrimPrefix(phone, "+")))
		}
	}
	slices.Sort(identifiers)
	identifiers = slices.Compact(identifiers)

	var avatar *bridgev2.Avatar
	if p, ok := user.GetPhoto(); ok && p.TypeID() == tg.UserProfilePhotoTypeID {
		photo := p.(*tg.UserProfilePhoto)
		avatar = &bridgev2.Avatar{
			ID: ids.MakeAvatarID(photo.PhotoID),
			Get: func(ctx context.Context) (data []byte, err error) {
				data, _, err = media.NewTransferer(t.client.API()).WithUserPhoto(user, photo.PhotoID).Download(ctx)
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
		ExtraUpdates: func(ctx context.Context, ghost *bridgev2.Ghost) (changed bool) {
			meta := ghost.Metadata.(*GhostMetadata)
			if !user.Min {
				changed = changed || meta.IsPremium != user.Premium || meta.IsBot != user.Bot || meta.Username != user.Username
				meta.IsPremium = user.Premium
				meta.IsBot = user.Bot
				meta.Username = user.Username
			}
			changed = changed || meta.AccessHash != user.AccessHash
			meta.AccessHash = user.AccessHash
			return changed
		},
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
