package connector

import (
	"context"
	"errors"
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/gotd/td/telegram"
	"github.com/gotd/td/telegram/updates"
	"github.com/gotd/td/tg"
	"github.com/rs/zerolog"
	"go.mau.fi/util/exsync"
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

	appConfigLock sync.Mutex
	appConfig     map[string]any
	appConfigHash int

	availableReactionsLock    sync.Mutex
	availableReactions        map[string]struct{}
	availableReactionsHash    int
	availableReactionsFetched time.Time

	telegramFmtParams *telegramfmt.FormatParams
	matrixParser      *matrixfmt.HTMLParser

	cachedContacts     *tg.ContactsContacts
	cachedContactsHash int64

	takeoutLock        sync.Mutex
	takeoutAccepted    *exsync.Event
	stopTakeoutTimer   *time.Timer
	takeoutDialogsOnce sync.Once

	prevReactionPoll map[networkid.PortalKey]time.Time
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
	_ bridgev2.MuteHandlingNetworkAPI          = (*TelegramClient)(nil)
	_ bridgev2.TagHandlingNetworkAPI           = (*TelegramClient)(nil)
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
		ScopedStore: tc.Store.GetScopedStore(telegramUserID),

		main:           tc,
		telegramUserID: telegramUserID,
		loginID:        login.ID,
		userID:         networkid.UserID(login.ID),
		userLogin:      login,

		takeoutAccepted: exsync.NewEvent(),

		prevReactionPoll: map[networkid.PortalKey]time.Time{},
	}

	if !login.Metadata.(*UserLoginMetadata).Session.HasAuthKey() {
		return &client, nil
	}

	dispatcher := UpdateDispatcher{
		UpdateDispatcher: tg.NewUpdateDispatcher(),
		EntityHandler:    client.onEntityUpdate,
	}
	dispatcher.OnNewMessage(func(ctx context.Context, e tg.Entities, update *tg.UpdateNewMessage) error {
		return client.onUpdateNewMessage(ctx, e.Channels, update)
	})
	dispatcher.OnChannel(func(ctx context.Context, e tg.Entities, update *tg.UpdateChannel) error {
		return client.onUpdateChannel(ctx, update)
	})
	dispatcher.OnNewChannelMessage(func(ctx context.Context, e tg.Entities, update *tg.UpdateNewChannelMessage) error {
		return client.onUpdateNewMessage(ctx, e.Channels, update)
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
		return client.handleTyping(client.makePortalKeyFromID(ids.PeerTypeUser, update.UserID), client.senderForUserID(update.UserID), update.Action)
	})
	dispatcher.OnChatUserTyping(func(ctx context.Context, e tg.Entities, update *tg.UpdateChatUserTyping) error {
		if update.FromID.TypeID() != tg.PeerUserTypeID {
			log.Warn().Str("from_id_type", update.FromID.TypeName()).Msg("unsupported from_id type")
			return nil
		}
		return client.handleTyping(client.makePortalKeyFromID(ids.PeerTypeChat, update.ChatID), client.getPeerSender(update.FromID), update.Action)
	})
	dispatcher.OnChannelUserTyping(func(ctx context.Context, e tg.Entities, update *tg.UpdateChannelUserTyping) error {
		return client.handleTyping(client.makePortalKeyFromID(ids.PeerTypeChannel, update.ChannelID), client.getPeerSender(update.FromID), update.Action)
	})
	dispatcher.OnReadHistoryOutbox(func(ctx context.Context, e tg.Entities, update *tg.UpdateReadHistoryOutbox) error {
		return client.updateReadReceipt(update)
	})
	dispatcher.OnReadHistoryInbox(func(ctx context.Context, e tg.Entities, update *tg.UpdateReadHistoryInbox) error {
		return client.onOwnReadReceipt(client.makePortalKeyFromPeer(update.Peer), update.MaxID)
	})
	dispatcher.OnReadChannelInbox(func(ctx context.Context, e tg.Entities, update *tg.UpdateReadChannelInbox) error {
		return client.onOwnReadReceipt(client.makePortalKeyFromID(ids.PeerTypeChannel, update.ChannelID), update.MaxID)
	})
	dispatcher.OnNotifySettings(func(ctx context.Context, e tg.Entities, update *tg.UpdateNotifySettings) error {
		return client.onNotifySettings(ctx, update)
	})
	dispatcher.OnPinnedDialogs(func(ctx context.Context, e tg.Entities, update *tg.UpdatePinnedDialogs) error {
		return client.onPinnedDialogs(ctx, update)
	})
	dispatcher.OnChatDefaultBannedRights(func(ctx context.Context, e tg.Entities, update *tg.UpdateChatDefaultBannedRights) error {
		return client.onChatDefaultBannedRights(ctx, e, update)
	})
	dispatcher.OnPeerBlocked(func(ctx context.Context, e tg.Entities, update *tg.UpdatePeerBlocked) error {
		return client.onPeerBlocked(ctx, update)
	})
	dispatcher.OnChat(client.onChat)
	dispatcher.OnPhoneCall(client.onPhoneCall)

	client.updatesManager = updates.New(updates.Config{
		OnChannelTooLong: func(channelID int64) {
			tc.Bridge.QueueRemoteEvent(login, &simplevent.ChatResync{
				EventMeta: simplevent.EventMeta{
					Type: bridgev2.RemoteEventChatResync,
					LogContext: func(c zerolog.Context) zerolog.Context {
						return c.Str("update", "channel_too_long").Int64("channel_id", channelID)
					},
					PortalKey: client.makePortalKeyFromID(ids.PeerTypeChannel, channelID),
				},
				CheckNeedsBackfillFunc: func(ctx context.Context, latestMessage *database.Message) (bool, error) { return true, nil },
			})
		},
		Handler:      dispatcher,
		Logger:       zaplog.Named("gaps"),
		Storage:      client.ScopedStore,
		AccessHasher: client.ScopedStore,
	})

	client.client = telegram.NewClient(tc.Config.APIID, tc.Config.APIHash, telegram.Options{
		CustomSessionStorage: &login.Metadata.(*UserLoginMetadata).Session,
		Logger:               zaplog,
		UpdateHandler:        client.updatesManager,
		OnDead:               client.onDead,
		OnSession:            client.onConnectionStateChange("session"),
		OnConnected:          client.onConnectionStateChange("connected"),
		PingCallback:         client.onConnectionStateChange("ping"),
		OnAuthError:          client.onAuthError,
		PingTimeout:          time.Duration(tc.Config.Ping.TimeoutSeconds) * time.Second,
		PingInterval:         time.Duration(tc.Config.Ping.IntervalSeconds) * time.Second,
		Device: telegram.DeviceConfig{
			DeviceModel:    tc.Config.DeviceInfo.DeviceModel,
			SystemVersion:  tc.Config.DeviceInfo.SystemVersion,
			AppVersion:     tc.Config.DeviceInfo.AppVersion,
			SystemLangCode: tc.Config.DeviceInfo.SystemLangCode,
			LangCode:       tc.Config.DeviceInfo.LangCode,
		},
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
			if peerType, userID, err := client.ScopedStore.GetEntityIDByUsername(ctx, username); err != nil {
				return telegramfmt.UserInfo{}, err
			} else if peerType != ids.PeerTypeUser {
				return telegramfmt.UserInfo{}, fmt.Errorf("unexpected peer type: %s", peerType)
			} else if ghost, err := tc.Bridge.GetGhostByID(ctx, ids.MakeUserID(userID)); err != nil {
				return telegramfmt.UserInfo{}, err
			} else {
				userInfo := telegramfmt.UserInfo{MXID: ghost.Intent.GetMXID(), Name: ghost.Name}
				if ghost.ID == client.userID {
					userInfo.MXID = client.userLogin.UserMXID
				}
				return userInfo, nil
			}
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
				portalKey = client.makePortalKeyFromID(ids.PeerTypeChannel, chatID)
			} else {
				userID, err := strconv.ParseInt(submatches[1], 10, 64)
				if err != nil {
					log.Err(err).Msg("error parsing user ID")
					return url
				}
				portalKey = client.makePortalKeyFromID(ids.PeerTypeUser, userID)
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
			} else if message == nil {
				log.Err(err).Msg("message not found")
				return url
			}

			return portal.MXID.EventURI(message.MXID, tc.Bridge.Matrix.ServerName()).MatrixToURL()
		},
	}
	client.matrixParser = &matrixfmt.HTMLParser{
		GetGhostDetails: func(ctx context.Context, ui id.UserID) (networkid.UserID, string, int64, bool) {
			if userID, ok := tc.Bridge.Matrix.ParseGhostMXID(ui); !ok {
				return "", "", 0, false
			} else if peerType, telegramUserID, err := ids.ParseUserID(userID); err != nil {
				return "", "", 0, false
			} else if accessHash, err := client.ScopedStore.GetAccessHash(ctx, peerType, telegramUserID); err != nil || accessHash == 0 {
				return "", "", 0, false
			} else if username, err := client.ScopedStore.GetUsername(ctx, peerType, telegramUserID); err != nil {
				return "", "", 0, false
			} else {
				return userID, username, accessHash, true
			}
		},
	}

	return &client, err
}

// connectTelegramClient blocks until client is connected, calling Run
// internally.
// Technique from: https://github.com/gotd/contrib/blob/master/bg/connect.go
func connectTelegramClient(ctx context.Context, client *telegram.Client) (context.Context, context.CancelFunc, error) {
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
		return nil, func() {}, ctx.Err()
	case err := <-errC: // startup timeout
		cancel()
		return nil, func() {}, err
	case <-initDone: // init done
	}

	return ctx, cancel, nil
}

func (t *TelegramClient) onDead() {
	t.userLogin.BridgeState.Send(status.BridgeState{
		StateEvent: status.StateTransientDisconnect,
		Message:    "Telegram client disconnected",
	})
}

func (t *TelegramClient) sendBadCredentials(message string) {
	t.userLogin.BridgeState.Send(status.BridgeState{
		StateEvent: status.StateBadCredentials,
		Error:      "tg-no-auth",
		Message:    message,
	})
}

func (t *TelegramClient) sendUnknownError(message string) {
	t.userLogin.BridgeState.Send(status.BridgeState{
		StateEvent: status.StateUnknownError,
		Error:      "tg-not-authenticated",
		Message:    message,
	})
}

func (t *TelegramClient) onConnectionStateChange(reason string) func() {
	return func() {
		log := t.main.Bridge.Log.With().
			Str("component", "telegram_client").
			Str("user_login_id", string(t.userLogin.ID)).
			Str("reason", reason).
			Logger()
		log.Info().Msg("Connection state changed")
		ctx := log.WithContext(context.Background())

		authStatus, err := t.client.Auth().Status(ctx)
		if err != nil {
			t.sendUnknownError(err.Error())
		} else if authStatus.Authorized {
			t.userLogin.BridgeState.Send(status.BridgeState{StateEvent: status.StateConnected})
		} else {
			t.sendBadCredentials("You're not logged in")
			t.userLogin.Metadata.(*UserLoginMetadata).Session.AuthKey = nil
			t.client = nil
			if err := t.userLogin.Save(ctx); err != nil {
				log.Err(err).Msg("failed to save user login")
			}
		}
	}
}

func (t *TelegramClient) onAuthError(err error) {
	t.sendBadCredentials(err.Error())
	t.userLogin.Metadata.(*UserLoginMetadata).Session.AuthKey = nil
	t.client = nil
	if err := t.userLogin.Save(context.Background()); err != nil {
		t.main.Bridge.Log.Err(err).Msg("failed to save user login")
	}
}

func (t *TelegramClient) Connect(ctx context.Context) error {
	if !t.userLogin.Metadata.(*UserLoginMetadata).Session.HasAuthKey() {
		t.sendBadCredentials("User does not have an auth key")
		return nil
	}

	var err error
	ctx, t.clientCancel, err = connectTelegramClient(ctx, t.client)
	if err != nil {
		t.sendUnknownError(err.Error())
		return nil
	}
	go func() {
		err = t.updatesManager.Run(ctx, t.client.API(), t.telegramUserID, updates.AuthOptions{})
		if err != nil {
			zerolog.Ctx(ctx).Err(err).Msg("failed to run updates manager")
			t.clientCancel()
		}
	}()

	// Update the logged-in user's ghost info (this also updates the user
	// login's remote name and profile).
	if me, err := t.client.Self(ctx); err != nil {
		t.sendUnknownError(fmt.Sprintf("failed to get self: %v", err))
	} else if _, err := t.updateGhost(ctx, t.telegramUserID, me); err != nil {
		t.sendUnknownError(fmt.Sprintf("failed to update own ghost: %v", err))
	} else {
		t.userLogin.BridgeState.Send(status.BridgeState{StateEvent: status.StateConnected})
	}

	// Fix the "Telegram Saved Messages" chat
	t.main.Bridge.QueueRemoteEvent(t.userLogin, &simplevent.ChatResync{
		ChatInfo: t.getDMChatInfo(t.telegramUserID),
		EventMeta: simplevent.EventMeta{
			Type:         bridgev2.RemoteEventChatResync,
			PortalKey:    t.makePortalKeyFromID(ids.PeerTypeUser, t.telegramUserID),
			CreatePortal: false, // Do not create the portal if it doesn't already exist
		},
	})
	return nil
}

func (t *TelegramClient) Disconnect() {
	if t.clientCancel != nil {
		t.clientCancel()
	}
}

func (t *TelegramClient) getInputUser(ctx context.Context, id int64) (*tg.InputUser, error) {
	accessHash, err := t.ScopedStore.GetAccessHash(ctx, ids.PeerTypeUser, id)
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
		// TODO does this mean the user is deleted? Need to handle this a bit better
		return nil, fmt.Errorf("failed to get user info for user %d", id)
	} else {
		return users[0], nil
	}
}

func (t *TelegramClient) getSingleChannel(ctx context.Context, id int64) (*tg.Channel, error) {
	accessHash, err := t.ScopedStore.GetAccessHash(ctx, ids.PeerTypeChannel, id)
	if err != nil {
		return nil, err
	}
	chats, err := APICallWithOnlyChatUpdates(ctx, t, func() (tg.MessagesChatsClass, error) {
		return t.client.API().ChannelsGetChannels(ctx, []tg.InputChannelClass{
			&tg.InputChannel{ChannelID: id, AccessHash: accessHash},
		})
	})
	if err != nil {
		return nil, err
	} else if len(chats.GetChats()) == 0 {
		return nil, fmt.Errorf("failed to get channel info for channel %d", id)
	} else if channel, ok := chats.GetChats()[0].(*tg.Channel); !ok {
		return nil, fmt.Errorf("unexpected channel type %T", chats.GetChats()[id])
	} else {
		return channel, nil
	}
}

func (t *TelegramClient) GetUserInfo(ctx context.Context, ghost *bridgev2.Ghost) (*bridgev2.UserInfo, error) {
	peerType, id, err := ids.ParseUserID(ghost.ID)
	if err != nil {
		return nil, err
	}
	switch peerType {
	case ids.PeerTypeUser:
		if user, err := t.getSingleUser(ctx, id); err != nil {
			return nil, fmt.Errorf("failed to get user %d: %w", id, err)
		} else if user.TypeID() != tg.UserTypeID {
			return nil, err
		} else {
			return t.updateGhost(ctx, id, user.(*tg.User))
		}
	case ids.PeerTypeChannel:
		if channel, err := t.getSingleChannel(ctx, id); err != nil {
			return nil, fmt.Errorf("failed to get channel %d: %w", id, err)
		} else if channel.TypeID() != tg.ChannelTypeID {
			return nil, err
		} else {
			return t.updateChannel(ctx, channel)
		}
	default:
		return nil, fmt.Errorf("unexpected peer type: %s", peerType)
	}
}

func (t *TelegramClient) getUserInfoFromTelegramUser(ctx context.Context, u tg.UserClass) (*bridgev2.UserInfo, error) {
	user, ok := u.(*tg.User)
	if !ok {
		return nil, fmt.Errorf("user is %T not *tg.User", user)
	}
	var identifiers []string
	if !user.Min {
		if accessHash, ok := user.GetAccessHash(); ok {
			if err := t.ScopedStore.SetAccessHash(ctx, ids.PeerTypeUser, user.ID, accessHash); err != nil {
				return nil, err
			}
		}

		if err := t.ScopedStore.SetUsername(ctx, ids.PeerTypeUser, user.ID, user.Username); err != nil {
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
				return transferer.DownloadBytes(ctx)
			},
		}
	}

	name := util.FormatFullName(user.FirstName, user.LastName, user.Deleted, user.ID)
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
				meta.Deleted = user.Deleted
			}
			return changed
		},
	}, nil
}

func (t *TelegramClient) IsLoggedIn() bool {
	if t == nil {
		return false
	}
	t.main.Bridge.Log.Debug().
		Bool("has_client", t.client != nil).
		Bool("has_auth_key", t.userLogin.Metadata.(*UserLoginMetadata).Session.HasAuthKey()).
		Msg("Checking if user is logged in")
	return t.client != nil && t.userLogin.Metadata.(*UserLoginMetadata).Session.HasAuthKey()
}

func (t *TelegramClient) LogoutRemote(ctx context.Context) {
	log := zerolog.Ctx(ctx).With().
		Str("action", "logout_remote").
		Int64("user_id", t.telegramUserID).
		Logger()
	log.Info().Msg("Logging out")

	err := t.ScopedStore.DeleteUserState(ctx)
	if err != nil {
		log.Err(err).Msg("failed to delete user state")
	}

	err = t.ScopedStore.DeleteChannelStateForUser(ctx)
	if err != nil {
		log.Err(err).Msg("failed to delete channel state for user")
	}

	err = t.ScopedStore.DeleteAccessHashesForUser(ctx)
	if err != nil {
		log.Err(err).Msg("failed to delete access hashes for user")
	}

	if !t.userLogin.Metadata.(*UserLoginMetadata).Session.HasAuthKey() {
		log.Info().Msg("User does not have an auth key, not logging out")
		return
	}

	_, err = t.client.API().AuthLogOut(ctx)
	if err != nil {
		log.Err(err).Msg("failed to logout on Telegram")
	}

	log.Info().Msg("successfully logged out and deleted user state")
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

func (t *TelegramClient) senderForUserID(userID int64) bridgev2.EventSender {
	return bridgev2.EventSender{
		IsFromMe:    userID == t.telegramUserID,
		SenderLogin: ids.MakeUserLoginID(userID),
		Sender:      ids.MakeUserID(userID),
	}
}
