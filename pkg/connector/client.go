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
	"cmp"
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
	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram"
	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/auth"
	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/updates"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

var (
	ErrNoAuthKey        = errors.New("user does not have auth key")
	ErrFailToQueueEvent = errors.New("failed to queue event")
)

func resultToError(res bridgev2.EventHandlingResult) error {
	if !res.Success {
		if res.Error != nil {
			return fmt.Errorf("%w: %w", ErrFailToQueueEvent, res.Error)
		}
		return ErrFailToQueueEvent
	}
	return nil
}

type TelegramClient struct {
	main              *TelegramConnector
	ScopedStore       *store.ScopedStore
	telegramUserID    int64
	loginID           networkid.UserLoginID
	userID            networkid.UserID
	userLogin         *bridgev2.UserLogin
	metadata          *UserLoginMetadata
	client            *telegram.Client
	updatesManager    *updates.Manager
	dispatcher        tg.UpdateDispatcher
	clientCtx         context.Context
	clientCancel      context.CancelFunc
	clientDone        *exsync.Event
	clientInitialized *exsync.Event
	mu                sync.Mutex

	appConfigLock sync.Mutex
	appConfig     map[string]any
	appConfigHash int

	availableReactionsLock    sync.Mutex
	availableReactions        map[string]struct{}
	availableReactionsHash    int
	availableReactionsFetched time.Time
	availableReactionsList    []string
	isPremiumCache            atomic.Bool

	recentMessageRooms *exsync.RingBuffer[networkid.MessageID, networkid.PortalKey]

	telegramFmtParams *telegramfmt.FormatParams
	matrixParser      *matrixfmt.HTMLParser

	cachedContacts     *tg.ContactsContacts
	cachedContactsHash int64
	contactsLock       sync.Mutex
	lastContactReq     time.Time

	dcTransferLock sync.Mutex

	takeoutLock        sync.Mutex
	takeoutAccepted    *exsync.Event
	stopTakeoutTimer   *time.Timer
	takeoutDialogsOnce sync.Once
	syncChatsLock      sync.Mutex
	isNewLogin         bool

	prevReactionPoll     map[networkid.PortalKey]time.Time
	prevReactionPollLock sync.Mutex

	stickerPackCache     map[string]map[int64]*tg.Document
	stickerPackCacheLock sync.Mutex
}

var _ bridgev2.NetworkAPI = (*TelegramClient)(nil)

var messageLinkRegex = regexp.MustCompile(`^https?://t(?:elegram)?\.(?:me|dog)/([A-Za-z][A-Za-z0-9_]{3,31}[A-Za-z0-9]|[Cc]/[0-9]{1,20})/([0-9]{1,20})(?:/([0-9]{1,20}))?$`)

func (tc *TelegramConnector) deviceConfig() telegram.DeviceConfig {
	return telegram.DeviceConfig{
		DeviceModel:    tc.Config.DeviceInfo.DeviceModel,
		SystemVersion:  tc.Config.DeviceInfo.SystemVersion,
		AppVersion:     tc.Config.DeviceInfo.AppVersion,
		SystemLangCode: tc.Config.DeviceInfo.SystemLangCode,
		LangCode:       tc.Config.DeviceInfo.LangCode,
	}
}

var zapLevelMap = map[zapcore.Level]zerolog.Level{
	// shifted
	zapcore.DebugLevel: zerolog.TraceLevel,
	zapcore.InfoLevel:  zerolog.DebugLevel,

	// direct mapping
	zapcore.WarnLevel:   zerolog.WarnLevel,
	zapcore.ErrorLevel:  zerolog.ErrorLevel,
	zapcore.DPanicLevel: zerolog.PanicLevel,
	zapcore.PanicLevel:  zerolog.PanicLevel,
	zapcore.FatalLevel:  zerolog.FatalLevel,
}

func NewTelegramClient(ctx context.Context, tc *TelegramConnector, login *bridgev2.UserLogin) (*TelegramClient, error) {
	telegramUserID, err := ids.ParseUserLoginID(login.ID)
	if err != nil {
		return nil, err
	}

	log := zerolog.Ctx(ctx).With().
		Str("component", "telegram_client").
		Str("user_login_id", string(login.ID)).
		Logger()

	zaplog := zap.New(zerozap.NewWithLevels(log, zapLevelMap))

	client := TelegramClient{
		ScopedStore: tc.Store.GetScopedStore(telegramUserID),

		main:           tc,
		telegramUserID: telegramUserID,
		loginID:        login.ID,
		userID:         networkid.UserID(login.ID),
		userLogin:      login,
		metadata:       login.Metadata.(*UserLoginMetadata),

		takeoutAccepted: exsync.NewEvent(),

		prevReactionPoll: map[networkid.PortalKey]time.Time{},
		stickerPackCache: map[string]map[int64]*tg.Document{},

		recentMessageRooms: exsync.NewRingBuffer[networkid.MessageID, networkid.PortalKey](32),

		clientInitialized: exsync.NewEvent(),
		clientDone:        exsync.NewEvent(),
	}

	if !login.Metadata.(*UserLoginMetadata).Session.HasAuthKey() {
		return &client, nil
	}

	client.dispatcher = tg.NewUpdateDispatcher()
	client.dispatcher.OnFallback(client.onUpdateWrapper)

	client.updatesManager = updates.New(updates.Config{
		OnNotChannelMember: client.onNotChannelMember,
		OnChannelTooLong: func(channelID int64) error {
			// TODO resync topics?
			res := tc.Bridge.QueueRemoteEvent(login, &simplevent.ChatResync{
				EventMeta: simplevent.EventMeta{
					Type: bridgev2.RemoteEventChatResync,
					LogContext: func(c zerolog.Context) zerolog.Context {
						return c.Str("update", "channel_too_long").Int64("channel_id", channelID)
					},
					PortalKey: client.makePortalKeyFromID(ids.PeerTypeChannel, channelID, 0),
				},
				CheckNeedsBackfillFunc: func(ctx context.Context, latestMessage *database.Message) (bool, error) {
					return true, nil
				},
			})

			return resultToError(res)
		},
		Handler:      client.dispatcher,
		Logger:       zaplog.Named("gaps"),
		Storage:      client.ScopedStore,
		AccessHasher: client.ScopedStore,
	})
	resolver, err := GetProxyResolver(tc.Config.ProxyConfig)
	if err != nil {
		return nil, err
	}
	client.client = telegram.NewClient(tc.Config.APIID, tc.Config.APIHash, telegram.Options{
		CustomSessionStorage: &login.Metadata.(*UserLoginMetadata).Session,
		Logger:               zaplog,
		UpdateHandler:        client.updatesManager,
		Resolver:             resolver,
		OnDead:               client.onDead,
		OnSession:            client.onSession,
		OnConnected:          client.onConnected,
		OnTransfer:           client.onTransfer,
		PingCallback:         client.onPing,
		OnAuthError:          client.onAuthError,
		PingTimeout:          time.Duration(tc.Config.Ping.TimeoutSeconds) * time.Second,
		PingInterval:         time.Duration(tc.Config.Ping.IntervalSeconds) * time.Second,
		Device:               tc.deviceConfig(),
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
			} else if login := tc.Bridge.GetCachedUserLoginByID(ids.MakeUserLoginID(id)); login != nil {
				userInfo.MXID = login.UserMXID
			}
			return userInfo, nil
		},
		GetUserInfoByUsername: func(ctx context.Context, username string) (telegramfmt.UserInfo, error) {
			if peerType, userID, err := client.main.Store.Username.GetEntityID(ctx, username); err != nil {
				return telegramfmt.UserInfo{}, err
			} else if peerType != ids.PeerTypeUser {
				return telegramfmt.UserInfo{}, fmt.Errorf("unexpected peer type: %s", peerType)
			} else if ghost, err := tc.Bridge.GetGhostByID(ctx, ids.MakeUserID(userID)); err != nil {
				return telegramfmt.UserInfo{}, err
			} else {
				userInfo := telegramfmt.UserInfo{MXID: ghost.Intent.GetMXID(), Name: ghost.Name}
				if ghost.ID == client.userID {
					userInfo.MXID = client.userLogin.UserMXID
				} else if login := tc.Bridge.GetCachedUserLoginByID(ids.MakeUserLoginID(userID)); login != nil {
					userInfo.MXID = login.UserMXID
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
				log.Trace().Err(err).Str("url", url).Msg("Failed to parse message/topic ID in t.me link")
				return url
			}
			var topicID int
			if len(submatches) == 4 && submatches[3] != "" {
				lastID, err := strconv.Atoi(submatches[3])
				if err != nil {
					log.Trace().Err(err).Str("url", url).Msg("Failed to parse message ID in t.me link")
					return url
				}
				topicID = msgID
				msgID = lastID
			}
			log = log.With().Str("group", group).Int("topic_id", topicID).Int("msg_id", msgID).Logger()

			var portalKey networkid.PortalKey
			if strings.HasPrefix(group, "C/") || strings.HasPrefix(group, "c/") {
				chatID, err := strconv.ParseInt(submatches[1][2:], 10, 64)
				if err != nil {
					log.Trace().Err(err).Str("url", url).Msg("Failed to parse channel ID in t.me link")
					return url
				}
				portalKey = client.makePortalKeyFromID(ids.PeerTypeChannel, chatID, topicID)
			} else if submatches[1] == "premium" {
				portalKey = client.makePortalKeyFromID(ids.PeerTypeUser, 777000, 0)
			} else if userID, err := strconv.ParseInt(submatches[1], 10, 64); err == nil && userID > 0 {
				portalKey = client.makePortalKeyFromID(ids.PeerTypeUser, userID, 0)
			} else if peerType, peerID, err := client.main.Store.Username.GetEntityID(ctx, submatches[1]); err != nil {
				log.Err(err).Msg("Failed to get entity ID by username")
				return url
			} else if peerType != "" {
				portalKey = client.makePortalKeyFromID(peerType, peerID, topicID)
			} else {
				return url
			}

			portal, err := tc.Bridge.DB.Portal.GetByKey(ctx, portalKey)
			if err != nil {
				log.Err(err).Msg("Failed to get portal referenced by link in text")
				return url
			} else if portal == nil {
				log.Trace().
					Str("url", url).
					Msg("Portal referenced by link not found, using t.me link")
				return url
			}

			message, err := tc.Bridge.DB.Message.GetFirstPartByID(ctx, client.loginID, ids.MakeMessageID(portalKey, msgID))
			if err != nil {
				log.Err(err).Msg("Failed to get message referenced by link in text")
				return url
			} else if message == nil {
				log.Trace().
					Str("url", url).
					Msg("Message referenced by link not found, using t.me link")
				return url
			}

			return portal.MXID.EventURI(message.MXID, tc.Bridge.Matrix.ServerName()).MatrixToURL()
		},
	}
	client.matrixParser = &matrixfmt.HTMLParser{
		Store: tc.Store,
		GetGhostDetails: func(ctx context.Context, portal *bridgev2.Portal, ui id.UserID) (networkid.UserID, string, int64, bool) {
			userID, ok := tc.Bridge.Matrix.ParseGhostMXID(ui)
			if !ok {
				user, err := tc.Bridge.GetExistingUserByMXID(ctx, ui)
				if err != nil || user == nil {
					return "", "", 0, false
				} else if login, _, _ := portal.FindPreferredLogin(ctx, user, false); login != nil {
					userID = ids.UserLoginIDToUserID(login.ID)
				} else {
					return "", "", 0, false
				}
			}
			if peerType, telegramUserID, err := ids.ParseUserID(userID); err != nil {
				return "", "", 0, false
			} else if accessHash, err := client.ScopedStore.GetAccessHash(ctx, peerType, telegramUserID); err != nil || accessHash == 0 {
				return "", "", 0, false
			} else if username, err := client.main.Store.Username.Get(ctx, peerType, telegramUserID); err != nil {
				return "", "", 0, false
			} else {
				return userID, username, accessHash, true
			}
		},
	}

	return &client, err
}

func (tc *TelegramClient) onDead() {
	prevState := tc.userLogin.BridgeState.GetPrev().StateEvent
	if slices.Contains([]status.BridgeStateEvent{
		status.StateTransientDisconnect,
		status.StateBadCredentials,
		status.StateLoggedOut,
		status.StateUnknownError,
	}, prevState) {
		tc.userLogin.Log.Warn().
			Str("prev_state", string(prevState)).
			Msg("client is dead, not sending transient disconnect, because already in an error state")
		return
	}
	tc.userLogin.BridgeState.Send(status.BridgeState{
		StateEvent: status.StateTransientDisconnect,
		Message:    "Telegram client disconnected",
	})
}

func (tc *TelegramClient) sendBadCredentialsOrUnknownError(err error) {
	if auth.IsUnauthorized(err) || errors.Is(err, ErrNoAuthKey) {
		tc.userLogin.BridgeState.Send(status.BridgeState{
			StateEvent: status.StateBadCredentials,
			Error:      "tg-no-auth",
			Message:    humanise.Error(err),
		})
	} else {
		tc.userLogin.BridgeState.Send(status.BridgeState{
			StateEvent: status.StateUnknownError,
			Error:      "tg-unknown-error",
			Message:    humanise.Error(err),
			Info: map[string]any{
				"go_error": err.Error(),
			},
		})
	}
}

func (tc *TelegramClient) onPing() {
	prev := tc.userLogin.BridgeState.GetPrev()
	if prev.StateEvent == status.StateConnected || prev.Error == updateHandlerStuck {
		return
	}
	ctx := tc.userLogin.Log.WithContext(tc.main.Bridge.BackgroundCtx)
	tc.userLogin.Log.Debug().Msg("Got ping while not connected, checking auth")

	me, err := tc.client.Self(ctx)
	if auth.IsUnauthorized(err) {
		tc.onAuthError(err)
	} else if errors.Is(err, syscall.EPIPE) {
		// This is a pipe error, try disconnecting which will force the
		// updatesManager to fail and cause the client to reconnect.
		tc.userLogin.BridgeState.Send(status.BridgeState{
			StateEvent: status.StateTransientDisconnect,
			Error:      "pipe-error",
			Message:    humanise.Error(err),
		})
	} else if err != nil {
		tc.sendBadCredentialsOrUnknownError(err)
	} else {
		tc.onConnected(me)
	}
}

func (tc *TelegramConnector) userToRemoteProfile(
	self *tg.User,
	ghost *bridgev2.Ghost,
	prevState *status.RemoteProfile,
) (profile status.RemoteProfile, name string) {
	profile.Name = tc.Config.FormatDisplayname(self.FirstName, self.LastName, self.Username, self.Deleted, self.ID)
	if self.Phone != "" {
		profile.Phone = "+" + strings.TrimPrefix(self.Phone, "+")
	} else if prevState != nil {
		profile.Phone = prevState.Phone
	}
	profile.Username = self.Username
	if self.Username == "" && len(self.Usernames) > 0 {
		profile.Username = self.Usernames[0].Username
	}
	if ghost != nil {
		profile.Avatar = ghost.AvatarMXC
	} else if prevState != nil {
		profile.Avatar = prevState.Avatar
	}
	name = cmp.Or(profile.Username, profile.Phone, profile.Name)
	return
}

func (tc *TelegramClient) updateRemoteProfile(ctx context.Context, self *tg.User, ghost *bridgev2.Ghost) bool {
	newProfile, newName := tc.main.userToRemoteProfile(self, ghost, &tc.userLogin.RemoteProfile)
	if tc.userLogin.RemoteProfile != newProfile || tc.userLogin.RemoteName != newName {
		tc.userLogin.RemoteProfile = newProfile
		tc.userLogin.RemoteName = newName
		err := tc.userLogin.Save(ctx)
		if err != nil {
			tc.userLogin.Log.Err(err).Msg("Failed to save user login after profile update")
		}
		return true
	}
	return false
}

func (tc *TelegramClient) onConnected(self *tg.User) {
	log := tc.userLogin.Log
	ctx := log.WithContext(tc.main.Bridge.BackgroundCtx)
	ghost, err := tc.main.Bridge.GetGhostByID(ctx, tc.userID)
	if err != nil {
		log.Err(err).Msg("Failed to get own ghost")
	} else if wrapped, err := tc.wrapUserInfo(ctx, self, ghost); err != nil {
		log.Err(err).Msg("Failed to wrap own user info")
	} else {
		ghost.UpdateInfo(ctx, wrapped)
	}

	tc.updateRemoteProfile(ctx, self, ghost)
	tc.userLogin.BridgeState.Send(status.BridgeState{StateEvent: status.StateConnected})
}

func (tc *TelegramClient) onTransfer(ctx context.Context, _ *telegram.Client, fn func(context.Context) error) error {
	tc.userLogin.Log.Trace().Msg("Doing DC auth transfer")
	tc.dcTransferLock.Lock()
	defer tc.dcTransferLock.Unlock()
	return fn(ctx)
}

func (tc *TelegramClient) onSession() {
	tc.userLogin.Log.Debug().Msg("Got session created event")
}

func (tc *TelegramClient) onAuthError(err error) {
	tc.sendBadCredentialsOrUnknownError(err)
	tc.metadata.ResetOnLogout()
	go func() {
		tc.Disconnect()
		if err := tc.userLogin.Save(context.Background()); err != nil {
			tc.main.Bridge.Log.Err(err).Msg("failed to save user login")
		}
	}()
}

func (tc *TelegramClient) Connect(ctx context.Context) {
	tc.mu.Lock()
	defer tc.mu.Unlock()

	log := zerolog.Ctx(ctx)
	if !tc.metadata.Session.HasAuthKey() {
		log.Warn().Msg("user does not have an auth key, sending bad credentials state")
		tc.sendBadCredentialsOrUnknownError(ErrNoAuthKey)
		return
	}

	tc.userLogin.BridgeState.Send(status.BridgeState{StateEvent: status.StateConnecting})

	log.Info().Msg("Connecting client")

	// Add a cancellation layer we can use for explicit Disconnect

	ctx, cancel := context.WithCancel(ctx)
	tc.clientCtx = ctx
	tc.clientCancel = cancel
	tc.clientDone.Clear()
	tc.clientInitialized.Clear()
	go tc.runInBackground(ctx)
}

func (tc *TelegramClient) runInBackground(ctx context.Context) {
	log := zerolog.Ctx(ctx)
	err := tc.client.Run(ctx, func(ctx context.Context) error {
		tc.clientInitialized.Set()
		// If takeout dialog sync is enabled, we assume it'll resume from a getTakeoutID call.
		// If not, resume dialog sync manually here.
		if !tc.isNewLogin && !tc.main.Config.Takeout.DialogSync {
			go func() {
				if err := tc.syncChats(log.WithContext(tc.clientCtx), 0, false, false); err != nil {
					log.Err(err).Msg("Failed to resume chat sync")
				}
			}()
		}
		log.Info().Msg("Client running, starting updates")
		err := tc.updatesManager.Run(ctx, tc.client.API(), tc.telegramUserID, updates.AuthOptions{
			IsBot: tc.metadata.IsBot,
		})
		if err != nil && !errors.Is(err, ctx.Err()) {
			log.Warn().Err(err).AnErr("ctx_err", ctx.Err()).Msg("Update manager exited with error")
		} else {
			log.Info().AnErr("ctx_err", ctx.Err()).Msg("Update manager exited without error")
		}
		return err
	})
	tc.clientDone.Set()
	tc.clientInitialized.Set()
	if err != nil {
		log.Err(err).AnErr("ctx_err", ctx.Err()).Msg("Client exited with error")
		tc.sendBadCredentialsOrUnknownError(err)
	} else if ctx.Err() == nil {
		log.Warn().Msg("Client exited unexpectedly")
		tc.sendBadCredentialsOrUnknownError(fmt.Errorf("unexpectedly disconnected from Telegram"))
	} else {
		log.Debug().AnErr("ctx_err", ctx.Err()).Msg("Client exited without error")
	}
}

func (tc *TelegramClient) Disconnect() {
	tc.mu.Lock()
	defer tc.mu.Unlock()

	tc.userLogin.Log.Debug().Msg("Disconnecting client")

	if tc.clientCancel != nil {
		tc.clientCancel()
		tc.userLogin.Log.Debug().Msg("Waiting for client disconnection")
		<-tc.clientDone.GetChan()
	}

	tc.userLogin.Log.Info().Msg("Disconnect complete")
}

func (tc *TelegramClient) IsLoggedIn() bool {
	// TODO use less hacky check than context cancellation
	return tc != nil && tc.client != nil &&
		tc.clientInitialized.IsSet() && !tc.clientDone.IsSet() &&
		tc.metadata.Session.HasAuthKey()
}

func (tc *TelegramClient) LogoutRemote(ctx context.Context) {
	log := zerolog.Ctx(ctx).With().
		Str("action", "logout_remote").
		Int64("user_id", tc.telegramUserID).
		Logger()

	log.Info().Msg("Logging out and disconnecting")

	if tc.metadata.Session.HasAuthKey() {
		log.Info().Msg("User has an auth key, logging out")

		// logging out is best effort, we want to logout even if we can't call the endpoint
		ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
		defer cancel()

		_, err := tc.client.API().AuthLogOut(ctx)
		if err != nil {
			log.Err(err).Msg("failed to logout on Telegram")
		}
	}

	tc.Disconnect()

	log.Info().Msg("Deleting user state")

	err := tc.ScopedStore.DeleteUserState(ctx)
	if err != nil {
		log.Err(err).Msg("failed to delete user state")
	}

	err = tc.ScopedStore.DeleteChannelStateForUser(ctx)
	if err != nil {
		log.Err(err).Msg("failed to delete channel state for user")
	}

	err = tc.ScopedStore.DeleteAccessHashesForUser(ctx)
	if err != nil {
		log.Err(err).Msg("failed to delete access hashes for user")
	}

	log.Info().Msg("Logged out and deleted user state")
}

func (tc *TelegramClient) IsThisUser(ctx context.Context, userID networkid.UserID) bool {
	return userID == networkid.UserID(tc.userLogin.ID)
}

func (tc *TelegramClient) mySender() bridgev2.EventSender {
	return bridgev2.EventSender{
		IsFromMe:    true,
		SenderLogin: tc.loginID,
		Sender:      tc.userID,
	}
}

func (tc *TelegramClient) senderForUserID(userID int64) bridgev2.EventSender {
	return bridgev2.EventSender{
		IsFromMe:    userID == tc.telegramUserID,
		SenderLogin: ids.MakeUserLoginID(userID),
		Sender:      ids.MakeUserID(userID),
	}
}

func (tc *TelegramClient) FillBridgeState(state status.BridgeState) status.BridgeState {
	if state.Info == nil {
		state.Info = make(map[string]any)
	}
	state.Info["is_bot"] = tc.metadata.IsBot
	state.Info["login_method"] = tc.metadata.LoginMethod
	return state
}
