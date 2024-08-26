package connector

import (
	"context"
	"errors"
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/gotd/td/telegram"
	"github.com/gotd/td/telegram/updates"
	"github.com/gotd/td/tg"
	"github.com/rs/zerolog"
	"go.mau.fi/zerozap"
	"go.uber.org/zap"
	"maunium.net/go/mautrix/bridge/status"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/bridgev2/simplevent"
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
	updatesManager *updates.Manager
	clientCancel   context.CancelFunc

	appConfig     map[string]any
	appConfigHash int

	telegramFmtParams *telegramfmt.FormatParams
	matrixParser      *matrixfmt.HTMLParser

	cachedContacts     *tg.ContactsContacts
	cachedContactsHash int64
}

var (
	_ bridgev2.NetworkAPI                      = (*TelegramClient)(nil)
	_ bridgev2.EditHandlingNetworkAPI          = (*TelegramClient)(nil)
	_ bridgev2.ReactionHandlingNetworkAPI      = (*TelegramClient)(nil)
	_ bridgev2.RedactionHandlingNetworkAPI     = (*TelegramClient)(nil)
	_ bridgev2.ReadReceiptHandlingNetworkAPI   = (*TelegramClient)(nil)
	_ bridgev2.TypingHandlingNetworkAPI        = (*TelegramClient)(nil)
	_ bridgev2.BackfillingNetworkAPI           = (*TelegramClient)(nil)
	_ bridgev2.BackfillingNetworkAPIWithLimits = (*TelegramClient)(nil)
	_ bridgev2.IdentifierResolvingNetworkAPI   = (*TelegramClient)(nil)
	_ bridgev2.ContactListingNetworkAPI        = (*TelegramClient)(nil)
	_ bridgev2.UserSearchingNetworkAPI         = (*TelegramClient)(nil)
	_ bridgev2.GroupCreatingNetworkAPI         = (*TelegramClient)(nil)
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

var messageLinkRegex = regexp.MustCompile(`^https?://t(?:elegram)?\.(?:me|dog)/([A-Za-z][A-Za-z0-9_]{3,31}[A-Za-z0-9]|[Cc]/[0-9]{1,20})/([0-9]{1,20})$`)

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
		return client.onDeleteMessages(ctx, 0, update)
	})
	dispatcher.OnDeleteChannelMessages(func(ctx context.Context, e tg.Entities, update *tg.UpdateDeleteChannelMessages) error {
		return client.onDeleteMessages(ctx, update.ChannelID, update)
	})
	dispatcher.OnEditMessage(func(ctx context.Context, e tg.Entities, update *tg.UpdateEditMessage) error {
		return client.onMessageEdit(ctx, update)
	})
	dispatcher.OnEditChannelMessage(func(ctx context.Context, e tg.Entities, update *tg.UpdateEditChannelMessage) error {
		return client.onMessageEdit(ctx, update)
	})
	dispatcher.OnUserTyping(func(ctx context.Context, e tg.Entities, update *tg.UpdateUserTyping) error {
		return client.handleTyping(ids.PeerTypeUser.AsPortalKey(update.UserID, login.ID), update.UserID, update.Action)
	})
	dispatcher.OnChatUserTyping(func(ctx context.Context, e tg.Entities, update *tg.UpdateChatUserTyping) error {
		if update.FromID.TypeID() != tg.PeerUserTypeID {
			log.Warn().Str("from_id_type", update.FromID.TypeName()).Msg("unsupported from_id type")
			return nil
		}
		return client.handleTyping(ids.PeerTypeChat.AsPortalKey(update.ChatID, login.ID), update.FromID.(*tg.PeerUser).UserID, update.Action)
	})
	dispatcher.OnChannelUserTyping(func(ctx context.Context, e tg.Entities, update *tg.UpdateChannelUserTyping) error {
		return client.handleTyping(ids.PeerTypeChannel.AsPortalKey(update.ChannelID, ""), update.FromID.(*tg.PeerUser).UserID, update.Action)
	})
	dispatcher.OnReadHistoryOutbox(func(ctx context.Context, e tg.Entities, update *tg.UpdateReadHistoryOutbox) error {
		return client.updateReadReceipt(update)
	})
	dispatcher.OnReadHistoryInbox(func(ctx context.Context, e tg.Entities, update *tg.UpdateReadHistoryInbox) error {
		return client.onOwnReadReceipt(ids.MakePortalKey(update.Peer, login.ID), update.MaxID)
	})
	dispatcher.OnReadChannelInbox(func(ctx context.Context, e tg.Entities, update *tg.UpdateReadChannelInbox) error {
		return client.onOwnReadReceipt(ids.PeerTypeChannel.AsPortalKey(update.ChannelID, ""), update.MaxID)
	})

	client.ScopedStore = tc.Store.GetScopedStore(telegramUserID)

	client.updatesManager = updates.New(updates.Config{
		OnChannelTooLong: func(channelID int64) {
			tc.Bridge.QueueRemoteEvent(login, &simplevent.ChatResync{
				EventMeta: simplevent.EventMeta{
					Type: bridgev2.RemoteEventChatResync,
					LogContext: func(c zerolog.Context) zerolog.Context {
						return c.Str("update", "channel_too_long").Int64("channel_id", channelID)
					},
					PortalKey: ids.PeerTypeChannel.AsPortalKey(channelID, login.ID),
				},
				CheckNeedsBackfillFunc: func(ctx context.Context, latestMessage *database.Message) (bool, error) { return true, nil },
			})
		},
		Handler:      dispatcher,
		Logger:       zaplog.Named("gaps"),
		Storage:      client.ScopedStore,
		AccessHasher: client.ScopedStore,
	})

	client.client = telegram.NewClient(tc.Config.AppID, tc.Config.AppHash, telegram.Options{
		CustomSessionStorage: &login.Metadata.(*UserLoginMetadata).Session,
		Logger:               zaplog,
		UpdateHandler:        client.updatesManager,
		OnDead:               client.onDead,
		OnSession:            client.onSession,
		OnAuthError:          client.onAuthError,
		PingTimeout:          time.Duration(tc.Config.Ping.TimeoutSeconds) * time.Second,
		PingInterval:         time.Duration(tc.Config.Ping.IntervalSeconds) * time.Second,
	})

	client.telegramFmtParams = &telegramfmt.FormatParams{
		GetUserInfoByID: func(ctx context.Context, id int64) (telegramfmt.UserInfo, error) {
			ghost, err := tc.Bridge.GetGhostByID(ctx, ids.MakeUserID(id))
			if err != nil {
				return telegramfmt.UserInfo{}, err
			}
			userInfo := telegramfmt.UserInfo{MXID: ghost.Intent.GetMXID(), Name: ghost.Name}
			// FIXME this should look for user logins by ID, not hardcode the current user
			if id == client.telegramUserID {
				userInfo.MXID = client.userLogin.UserMXID
			}
			return userInfo, nil
		},
		GetUserInfoByUsername: func(ctx context.Context, username string) (telegramfmt.UserInfo, error) {
			// FIXME this should just query telegram_user_metadata by username
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
				chatID, err := strconv.ParseInt(submatches[1][2:], 10, 64)
				if err != nil {
					log.Err(err).Msg("error parsing channel ID")
					return url
				}
				portalKey = ids.PeerTypeChannel.AsPortalKey(chatID, "")
			} else {
				userID, err := strconv.ParseInt(submatches[1], 10, 64)
				if err != nil {
					log.Err(err).Msg("error parsing user ID")
					return url
				}
				portalKey = ids.PeerTypeUser.AsPortalKey(userID, login.ID)
			}

			portal, err := tc.Bridge.DB.Portal.GetByKey(ctx, portalKey)
			if err != nil {
				log.Err(err).Msg("error getting portal")
				return url
			} else if portal == nil {
				log.Warn().Msg("portal not found")
				return url
			}

			message, err := tc.Bridge.DB.Message.GetFirstPartByID(ctx, client.loginID, ids.MakeMessageID(portalKey, msgID))
			if err != nil {
				log.Err(err).Msg("error getting message")
				return url
			}

			return portal.MXID.EventURI(message.MXID, tc.Bridge.Matrix.ServerName()).MatrixToURL()
		},
	}
	client.matrixParser = &matrixfmt.HTMLParser{
		GetGhostDetails: func(ctx context.Context, ui id.UserID) (networkid.UserID, string, int64, bool) {
			userID, ok := tc.Bridge.Matrix.ParseGhostMXID(ui)
			if !ok {
				return "", "", 0, false
			}
			telegramUserID, err := ids.ParseUserID(userID)
			if err != nil {
				return "", "", 0, false
			}
			ss := tc.Store.GetScopedStore(telegramUserID)
			accessHash, err := ss.GetAccessHash(ctx, telegramUserID)
			if err != nil || accessHash == 0 {
				return "", "", 0, false
			}
			username, err := ss.GetUsername(ctx, telegramUserID)
			if err != nil {
				return "", "", 0, false
			}
			return userID, username, accessHash, true
		},
	}

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

func (t *TelegramClient) onDead() {
	t.userLogin.BridgeState.Send(status.BridgeState{
		StateEvent: status.StateTransientDisconnect,
		Message:    "Telegram client disconnected",
	})
}

func (t *TelegramClient) onSession() {
	authStatus, err := t.client.Auth().Status(context.Background())
	if err != nil {
		t.userLogin.BridgeState.Send(status.BridgeState{
			StateEvent: status.StateUnknownError,
			Error:      "tg-not-authenticated",
			Message:    err.Error(),
		})
	} else if authStatus.Authorized {
		t.userLogin.BridgeState.Send(status.BridgeState{StateEvent: status.StateConnected})
	} else {
		t.userLogin.BridgeState.Send(status.BridgeState{
			StateEvent: status.StateBadCredentials,
			Error:      "tg-no-auth",
			Message:    "You're not logged in",
		})
	}
}

func (t *TelegramClient) onAuthError(err error) {
	t.userLogin.BridgeState.Send(status.BridgeState{
		StateEvent: status.StateBadCredentials,
		Error:      "tg-no-auth",
		Message:    err.Error(),
	})
}

func (t *TelegramClient) Connect(ctx context.Context) (err error) {
	t.clientCancel, err = connectTelegramClient(ctx, t.client)
	if err != nil {
		return err
	}
	go func() {
		err = t.updatesManager.Run(ctx, t.client.API(), t.telegramUserID, updates.AuthOptions{})
		if err != nil {
			zerolog.Ctx(ctx).Err(err).Msg("failed to run updates manager")
			t.clientCancel()
		}
	}()
	return
}

func (t *TelegramClient) Disconnect() {
	t.clientCancel()
}

func (t *TelegramClient) getInputUser(ctx context.Context, id int64) (*tg.InputUser, error) {
	accessHash, err := t.ScopedStore.GetAccessHash(ctx, id)
	if err != nil {
		return nil, fmt.Errorf("failed to get access hash for user %d: %w", id, err)
	}
	return &tg.InputUser{UserID: id, AccessHash: accessHash}, nil
}

func (t *TelegramClient) getSingleUser(ctx context.Context, id int64) (tg.UserClass, error) {
	if inputUser, err := t.getInputUser(ctx, id); err != nil {
		return nil, err
	} else if users, err := t.client.API().UsersGetUsers(ctx, []tg.InputUserClass{inputUser}); err != nil {
		return nil, err
	} else if len(users) == 0 {
		return nil, fmt.Errorf("failed to get user info for user %d", id)
	} else {
		return users[0], nil
	}
}

func (t *TelegramClient) GetUserInfo(ctx context.Context, ghost *bridgev2.Ghost) (*bridgev2.UserInfo, error) {
	id, err := ids.ParseUserID(ghost.ID)
	if err != nil {
		return nil, err
	}
	if user, err := t.getSingleUser(ctx, id); err != nil {
		return nil, fmt.Errorf("failed to get user %d: %w", id, err)
	} else if userInfo, err := t.getUserInfoFromTelegramUser(ctx, user); err != nil {
		return nil, err
	} else {
		return userInfo, t.updateGhostWithUserInfo(ctx, id, userInfo)
	}
}

func (t *TelegramClient) getUserInfoFromTelegramUser(ctx context.Context, u tg.UserClass) (*bridgev2.UserInfo, error) {
	user, ok := u.(*tg.User)
	if !ok {
		return nil, fmt.Errorf("user is %T not *tg.User", user)
	}
	var identifiers []string
	if err := t.ScopedStore.SetAccessHash(ctx, user.ID, user.AccessHash); err != nil {
		return nil, err
	}
	if !user.Min {
		if err := t.ScopedStore.SetUsername(ctx, user.ID, user.Username); err != nil {
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
			if err := t.ScopedStore.SetPhoneNumber(ctx, user.ID, normalized); err != nil {
				return nil, err
			}
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
				transferer, err := media.NewTransferer(t.client.API()).WithUserPhoto(ctx, t.ScopedStore, user, photo.PhotoID)
				if err != nil {
					return nil, err
				}
				data, _, err = transferer.Download(ctx)
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
				changed = changed || meta.IsPremium != user.Premium || meta.IsBot != user.Bot
				meta.IsPremium = user.Premium
				meta.IsBot = user.Bot
			}
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

func (t *TelegramClient) mySender() bridgev2.EventSender {
	return bridgev2.EventSender{
		IsFromMe:    true,
		SenderLogin: t.loginID,
		Sender:      t.userID,
	}
}
