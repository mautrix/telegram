// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2025 Sumner Evans
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

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
	"sync/atomic"
	"syscall"
	"time"

	"github.com/gotd/td/telegram"
	"github.com/gotd/td/telegram/auth"
	"github.com/gotd/td/telegram/updates"
	"github.com/gotd/td/tg"
	"github.com/rs/zerolog"
	"go.mau.fi/util/exsync"
	"go.mau.fi/zerozap"
	"go.uber.org/zap"
	"go.uber.org/zap/zapcore"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/bridgev2/simplevent"
	"maunium.net/go/mautrix/bridgev2/status"
	"maunium.net/go/mautrix/id"

	"go.mau.fi/mautrix-telegram/pkg/connector/humanise"
	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/connector/matrixfmt"
	"go.mau.fi/mautrix-telegram/pkg/connector/store"
	"go.mau.fi/mautrix-telegram/pkg/connector/telegramfmt"
	"go.mau.fi/mautrix-telegram/pkg/connector/util"
)

var (
	ErrNoAuthKey        = errors.New("user does not have auth key")
	ErrFailToQueueEvent = errors.New("failed to queue event")
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
	updatesCloseC  chan struct{}
	clientCtx      context.Context
	clientCancel   context.CancelFunc
	clientCloseC   chan struct{}
	initialized    chan struct{}
	mu             sync.Mutex

	appConfigLock sync.Mutex
	appConfig     map[string]any
	appConfigHash int

	availableReactionsLock    sync.Mutex
	availableReactions        map[string]struct{}
	availableReactionsHash    int
	availableReactionsFetched time.Time
	availableReactionsList    []string
	isPremiumCache            atomic.Bool

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

	zaplog := zap.New(zerozap.NewWithLevels(log, map[zapcore.Level]zerolog.Level{
		// shifted
		zapcore.DebugLevel: zerolog.TraceLevel,
		zapcore.InfoLevel:  zerolog.DebugLevel,

		// direct mapping
		zapcore.WarnLevel:   zerolog.WarnLevel,
		zapcore.ErrorLevel:  zerolog.ErrorLevel,
		zapcore.DPanicLevel: zerolog.PanicLevel,
		zapcore.PanicLevel:  zerolog.PanicLevel,
		zapcore.FatalLevel:  zerolog.FatalLevel,
	}))

	client := TelegramClient{
		ScopedStore: tc.Store.GetScopedStore(telegramUserID),

		main:           tc,
		telegramUserID: telegramUserID,
		loginID:        login.ID,
		userID:         networkid.UserID(login.ID),
		userLogin:      login,

		takeoutAccepted: exsync.NewEvent(),

		prevReactionPoll: map[networkid.PortalKey]time.Time{},

		initialized: make(chan struct{}),
	}

	if !login.Metadata.(*UserLoginMetadata).Session.HasAuthKey() {
		return &client, nil
	}

	dispatcher := UpdateDispatcher{
		UpdateDispatcher: tg.NewUpdateDispatcher(),
		EntityHandler:    client.onEntityUpdate,
	}
	dispatcher.OnNewMessage(func(ctx context.Context, e tg.Entities, update *tg.UpdateNewMessage) error {
		return client.onUpdateNewMessage(ctx, e, update)
	})
	dispatcher.OnNewChannelMessage(func(ctx context.Context, e tg.Entities, update *tg.UpdateNewChannelMessage) error {
		return client.onUpdateNewMessage(ctx, e, update)
	})
	dispatcher.OnChannel(client.onUpdateChannel)
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
	dispatcher.OnReadHistoryOutbox(client.updateReadReceipt)
	dispatcher.OnReadHistoryInbox(func(ctx context.Context, e tg.Entities, update *tg.UpdateReadHistoryInbox) error {
		return client.onOwnReadReceipt(client.makePortalKeyFromPeer(update.Peer), update.MaxID)
	})
	dispatcher.OnReadChannelInbox(func(ctx context.Context, e tg.Entities, update *tg.UpdateReadChannelInbox) error {
		return client.onOwnReadReceipt(client.makePortalKeyFromID(ids.PeerTypeChannel, update.ChannelID), update.MaxID)
	})
	dispatcher.OnNotifySettings(client.onNotifySettings)
	dispatcher.OnPinnedDialogs(client.onPinnedDialogs)
	dispatcher.OnChatDefaultBannedRights(client.onChatDefaultBannedRights)
	dispatcher.OnPeerBlocked(client.onPeerBlocked)
	dispatcher.OnChat(client.onChat)
	dispatcher.OnPhoneCall(client.onPhoneCall)

	client.updatesManager = updates.New(updates.Config{
		OnChannelTooLong: func(channelID int64) error {
			res := tc.Bridge.QueueRemoteEvent(login, &simplevent.ChatResync{
				EventMeta: simplevent.EventMeta{
					Type: bridgev2.RemoteEventChatResync,
					LogContext: func(c zerolog.Context) zerolog.Context {
						return c.Str("update", "channel_too_long").Int64("channel_id", channelID)
					},
					PortalKey: client.makePortalKeyFromID(ids.PeerTypeChannel, channelID),
				},
				CheckNeedsBackfillFunc: func(ctx context.Context, latestMessage *database.Message) (bool, error) { return true, nil },
			})

			if !res.Success {
				return ErrFailToQueueEvent
			}
			return nil
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
		PingCallback:         client.onPing,
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
			log = log.With().Str("group", group).Int("msg_id", msgID).Logger()

			var portalKey networkid.PortalKey
			if strings.HasPrefix(group, "C/") || strings.HasPrefix(group, "c/") {
				chatID, err := strconv.ParseInt(submatches[1][2:], 10, 64)
				if err != nil {
					log.Err(err).Msg("error parsing channel ID")
					return url
				}
				portalKey = client.makePortalKeyFromID(ids.PeerTypeChannel, chatID)
			} else if submatches[1] == "premium" {
				portalKey = client.makePortalKeyFromID(ids.PeerTypeUser, 777000)
			} else {
				userID, err := strconv.ParseInt(submatches[1], 10, 64)
				if err != nil {
					log.Warn().Err(err).Msg("error parsing user ID")
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
				log.Err(err).Msg("error getting referenced message")
				return url
			} else if message == nil {
				log.Warn().Err(err).Msg("message not found")
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
func connectTelegramClient(ctx context.Context, cancel context.CancelFunc, client *telegram.Client) (<-chan struct{}, error) {
	errC := make(chan error, 1)
	initDone := make(chan struct{})
	closeC := make(chan struct{})
	go func() {
		defer close(errC)
		defer close(closeC)
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
		return nil, fmt.Errorf("context cancelled before init done: %w", ctx.Err())
	case err := <-errC: // startup timeout
		cancel()
		return nil, fmt.Errorf("client connection timeout: %w", err)
	case <-initDone: // init done
	}
	return closeC, nil
}

func (t *TelegramClient) onDead() {
	prevState := t.userLogin.BridgeState.GetPrev().StateEvent
	if slices.Contains([]status.BridgeStateEvent{
		status.StateTransientDisconnect,
		status.StateBadCredentials,
		status.StateLoggedOut,
		status.StateUnknownError,
	}, prevState) {
		t.userLogin.Log.Warn().
			Str("prev_state", string(prevState)).
			Msg("client is dead, not sending transient disconnect, because already in an error state")
		return
	}
	t.userLogin.BridgeState.Send(status.BridgeState{
		StateEvent: status.StateTransientDisconnect,
		Message:    "Telegram client disconnected",
	})
}

func (t *TelegramClient) sendBadCredentialsOrUnknownError(err error) {
	if auth.IsUnauthorized(err) || errors.Is(err, ErrNoAuthKey) {
		t.userLogin.BridgeState.Send(status.BridgeState{
			StateEvent: status.StateBadCredentials,
			Error:      "tg-no-auth",
			Message:    humanise.Error(err),
		})
	} else {
		t.userLogin.BridgeState.Send(status.BridgeState{
			StateEvent: status.StateUnknownError,
			Error:      "tg-unknown-error",
			Message:    humanise.Error(err),
		})
	}
}

func (t *TelegramClient) onPing() {
	if t.userLogin.BridgeState.GetPrev().StateEvent == status.StateConnected {
		t.main.Bridge.Log.Trace().Msg("Got ping, not checking connectivity because we are already connected")
	} else {
		t.onConnectionStateChange("ping while not connected")
	}
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
			if errors.Is(err, syscall.EPIPE) {
				// This is a pipe error, try disconnecting which will force the
				// updatesManager to fail and cause the client to reconnect.
				t.userLogin.BridgeState.Send(status.BridgeState{
					StateEvent: status.StateTransientDisconnect,
					Error:      "pipe-error",
					Message:    humanise.Error(err),
				})
			} else {
				t.sendBadCredentialsOrUnknownError(err)
			}
		} else if authStatus.Authorized {
			t.userLogin.BridgeState.Send(status.BridgeState{StateEvent: status.StateConnected})
		} else {
			t.onAuthError(fmt.Errorf("not logged in"))
		}
	}
}

func (t *TelegramClient) onAuthError(err error) {
	t.sendBadCredentialsOrUnknownError(err)
	t.userLogin.Metadata.(*UserLoginMetadata).ResetOnLogout()
	go func() {
		t.Disconnect()
		if err := t.userLogin.Save(context.Background()); err != nil {
			t.main.Bridge.Log.Err(err).Msg("failed to save user login")
		}
	}()
}

func (t *TelegramClient) Connect(ctx context.Context) {
	t.mu.Lock()
	defer t.mu.Unlock()

	log := zerolog.Ctx(ctx).With().Int64("user_id", t.telegramUserID).Logger()

	if !t.userLogin.Metadata.(*UserLoginMetadata).Session.HasAuthKey() {
		log.Warn().Msg("user does not have an auth key, sending bad credentials state")
		t.sendBadCredentialsOrUnknownError(ErrNoAuthKey)
		return
	}

	log.Info().Msg("Connecting client")

	t.clientCtx, t.clientCancel = context.WithCancel(ctx)
	t.clientCloseC = make(chan struct{})
	t.updatesCloseC = make(chan struct{})
	go func() {
		defer close(t.initialized)
		connectClientCloseC, err := connectTelegramClient(t.clientCtx, t.clientCancel, t.client)
		if err != nil {
			t.sendBadCredentialsOrUnknownError(err)
			close(t.updatesCloseC)
			return
		}

		// awful hack to prevent assigning clientCloseC from racing Disconnect()
		go func() {
			<-connectClientCloseC
			close(t.clientCloseC)
		}()

		go func() {
			defer close(t.updatesCloseC)
			for {
				err = t.updatesManager.Run(t.clientCtx, t.client.API(), t.telegramUserID, updates.AuthOptions{})
				if err == nil || errors.Is(err, context.Canceled) {
					return
				}

				zerolog.Ctx(t.clientCtx).Err(err).Msg("failed to run updates manager, retrying")

				select {
				case <-t.clientCtx.Done():
					return
				case <-time.After(5 * time.Second):
				}
			}
		}()

		// Update the logged-in user's ghost info (this also updates the user
		// login's remote name and profile).
		if me, err := t.client.Self(t.clientCtx); err != nil {
			t.sendBadCredentialsOrUnknownError(err)
		} else if _, err := t.updateGhost(t.clientCtx, t.telegramUserID, me); err != nil {
			t.sendBadCredentialsOrUnknownError(err)
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
	}()
}

func (t *TelegramClient) Disconnect() {
	t.mu.Lock()
	defer t.mu.Unlock()

	t.userLogin.Log.Info().Msg("Disconnecting client")

	if t.clientCancel != nil {
		t.clientCancel()
		t.clientCancel = nil
	}
	if t.clientCloseC != nil {
		t.userLogin.Log.Debug().Msg("Waiting for client to finish")
		<-t.clientCloseC
		t.clientCloseC = nil
	}
	if t.updatesCloseC != nil {
		t.userLogin.Log.Debug().Msg("Waiting for updates to finish")
		<-t.updatesCloseC
		t.updatesCloseC = nil
	}

	t.userLogin.Log.Info().Msg("Disconnect complete")
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
		var err error
		avatar, err = t.convertUserProfilePhoto(ctx, user.ID, photo)
		if err != nil {
			return nil, err
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
				changed = changed || meta.IsPremium != user.Premium || meta.IsBot != user.Bot || meta.IsContact != user.Contact
				meta.IsPremium = user.Premium
				meta.IsBot = user.Bot
				meta.IsContact = user.Contact
				meta.Deleted = user.Deleted
			}
			return changed
		},
	}, nil
}

func (t *TelegramClient) IsLoggedIn() bool {
	if t == nil || t.clientCtx == nil {
		return false
	}
	select {
	case <-t.clientCtx.Done():
		t.main.Bridge.Log.Debug().
			Bool("client_context_done", true).
			Msg("Checking if user is logged in")
		return false
	default:
		t.main.Bridge.Log.Debug().
			Bool("has_client", t.client != nil).
			Bool("has_auth_key", t.userLogin.Metadata.(*UserLoginMetadata).Session.HasAuthKey()).
			Msg("Checking if user is logged in")
		return t.client != nil && t.userLogin.Metadata.(*UserLoginMetadata).Session.HasAuthKey()
	}
}

func (t *TelegramClient) LogoutRemote(ctx context.Context) {
	log := zerolog.Ctx(ctx).With().
		Str("action", "logout_remote").
		Int64("user_id", t.telegramUserID).
		Logger()

	log.Info().Msg("Logging out and disconnecting")

	if t.userLogin.Metadata.(*UserLoginMetadata).Session.HasAuthKey() {
		log.Info().Msg("User has an auth key, logging out")

		// logging out is best effort, we want to logout even if we can't call the endpoint
		ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
		defer cancel()

		_, err := t.client.API().AuthLogOut(ctx)
		if err != nil {
			log.Err(err).Msg("failed to logout on Telegram")
		}
	}

	t.Disconnect()

	log.Info().Msg("Deleting user state")

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

	log.Info().Msg("Logged out and deleted user state")
}

func (t *TelegramClient) IsThisUser(ctx context.Context, userID networkid.UserID) bool {
	return userID == networkid.UserID(t.userLogin.ID)
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
