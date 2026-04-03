// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2024 Sumner Evans
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
	"net/http"
	"sync"
	"time"

	"github.com/rs/zerolog"
	"go.mau.fi/zerozap"
	"go.uber.org/zap"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram"
	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/auth"
	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/updates"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

const (
	LoginFlowIDPhone    = "phone"
	LoginFlowIDQR       = "qr"
	LoginFlowIDBotToken = "bot"

	LoginStepIDComplete = "fi.mau.telegram.login.complete"
)

var (
	ErrInvalidPassword = bridgev2.RespError{
		ErrCode:    "FI.MAU.TELEGRAM.INVALID_PASSWORD",
		Err:        "Invalid password",
		StatusCode: http.StatusBadRequest,
	}
	ErrPhoneCodeInvalid = bridgev2.RespError{
		ErrCode:    "FI.MAU.TELEGRAM.PHONE_CODE_INVALID",
		Err:        "Invalid phone code",
		StatusCode: http.StatusBadRequest,
	}
	ErrSignUpNotSupported = bridgev2.RespError{
		ErrCode:    "FI.MAU.TELEGRAM.SIGN_UP_NOT_SUPPORTED",
		Err:        "New account creation is not supported",
		StatusCode: http.StatusBadRequest,
	}
)

func (tg *TelegramConnector) GetLoginFlows() []bridgev2.LoginFlow {
	return []bridgev2.LoginFlow{
		{
			Name:        "Phone Number",
			Description: "Login using your Telegram phone number",
			ID:          LoginFlowIDPhone,
		},
		{
			Name:        "QR Code",
			Description: "Login by scanning a QR code from your phone",
			ID:          LoginFlowIDQR,
		},
		{
			Name:        "Bot token",
			Description: "Log in as a bot using the bot token provided by BotFather.",
			ID:          LoginFlowIDBotToken,
		},
	}
}

func (tg *TelegramConnector) CreateLogin(ctx context.Context, user *bridgev2.User, flowID string) (bridgev2.LoginProcess, error) {
	bl := &baseLogin{
		user:   user,
		main:   tg,
		flowID: flowID,
	}
	switch flowID {
	case LoginFlowIDBotToken:
		return &BotLogin{baseLogin: bl}, nil
	case LoginFlowIDPhone:
		return &PhoneLogin{baseLogin: bl}, nil
	case LoginFlowIDQR:
		return &QRLogin{baseLogin: bl}, nil
	default:
		return nil, fmt.Errorf("unknown flow ID %s", flowID)
	}
}

type baseLogin struct {
	user    *bridgev2.User
	main    *TelegramConnector
	session UserLoginSession
	client  *telegram.Client
	ctx     context.Context
	cancel  context.CancelFunc
	flowID  string
}

func (bl *baseLogin) Cancel() {
	if bl.cancel != nil {
		bl.cancel()
	}
}

func (bl *baseLogin) makeClient(ctx context.Context, dispatcher *tg.UpdateDispatcher) error {
	log := zerolog.Ctx(ctx)
	zaplog := zap.New(zerozap.NewWithLevels(*log, zapLevelMap))
	var updateManager *updates.Manager
	if dispatcher != nil {
		updateManager = updates.New(updates.Config{
			Handler: dispatcher,
			Logger:  zaplog.Named("login_update_manager"),
		})
	}
	bl.client = telegram.NewClient(bl.main.Config.APIID, bl.main.Config.APIHash, telegram.Options{
		CustomSessionStorage: &bl.session,
		Logger:               zaplog,
		Device:               bl.main.deviceConfig(),
		UpdateHandler:        updateManager,
	})

	bl.ctx, bl.cancel = context.WithTimeoutCause(log.WithContext(bl.main.Bridge.BackgroundCtx), LoginTimeout, ErrLoginTimeout)
	connectResult := NewFuture[error]()
	go func() {
		err := bl.client.Run(bl.ctx, func(ctx context.Context) error {
			connectResult.Set(nil)
			<-ctx.Done()
			return ctx.Err()
		})
		connectResult.Set(err)
		if err != nil && !errors.Is(err, bl.ctx.Err()) {
			log.Err(err).Msg("Login client exited with error")
		}
	}()

	log.Debug().Msg("Waiting for client to connect")
	connErr, ctxErr := connectResult.Get(ctx)
	if err := cmp.Or(connErr, ctxErr); err != nil {
		bl.Cancel()
		return err
	}
	return nil
}

var passwordLoginStep = &bridgev2.LoginStep{
	Type:         bridgev2.LoginStepTypeUserInput,
	StepID:       LoginStepIDPassword,
	Instructions: "You have two-factor authentication enabled.",
	UserInputParams: &bridgev2.LoginUserInputParams{
		Fields: []bridgev2.LoginInputDataField{{
			Type: bridgev2.LoginInputFieldTypePassword,
			ID:   LoginStepIDPassword,
			Name: "Password",
		}},
	},
}

var passwordIncorrectLoginStep = &bridgev2.LoginStep{
	Type:         bridgev2.LoginStepTypeUserInput,
	StepID:       LoginStepIDPasswordIncorrect,
	Instructions: "Incorrect password, please try again. Use the official Telegram app to reset your password if you've forgotten it.",
	UserInputParams: &bridgev2.LoginUserInputParams{
		Fields: []bridgev2.LoginInputDataField{{
			Type: bridgev2.LoginInputFieldTypePassword,
			ID:   LoginStepIDPassword,
			Name: "Password",
		}},
	},
}

func (bl *baseLogin) submitPassword(ctx context.Context, password, loginPhone string) (*bridgev2.LoginStep, error) {
	if bl.client == nil {
		return nil, fmt.Errorf("unexpected state: client is nil when submitting password")
	} else if password == "" {
		return nil, fmt.Errorf("password not provided")
	}
	authorization, err := bl.client.Auth().Password(ctx, password)
	if err != nil {
		if errors.Is(err, auth.ErrPasswordInvalid) {
			return passwordIncorrectLoginStep, nil
		}
		bl.Cancel()
		return nil, fmt.Errorf("failed to submit password: %w", err)
	}
	return bl.finalizeLogin(ctx, authorization, &UserLoginMetadata{LoginPhone: loginPhone})
}

func (bl *baseLogin) finalizeLogin(
	ctx context.Context,
	authorization *tg.AuthAuthorization,
	metadata *UserLoginMetadata,
) (*bridgev2.LoginStep, error) {
	self, err := bl.client.Self(ctx)
	bl.Cancel()
	if err != nil {
		return nil, fmt.Errorf("failed to get self: %w", err)
	}
	if metadata == nil {
		metadata = &UserLoginMetadata{}
	}
	metadata.Session = bl.session
	metadata.LoginMethod = bl.flowID
	profile, name := bl.main.userToRemoteProfile(self, nil, nil)
	userLoginID := ids.MakeUserLoginID(authorization.User.GetID())
	ul, err := bl.user.NewLogin(ctx, &database.UserLogin{
		ID:            userLoginID,
		Metadata:      metadata,
		RemoteProfile: profile,
		RemoteName:    name,
	}, &bridgev2.NewLoginParams{
		DeleteOnConflict: true,
	})
	if err != nil {
		return nil, fmt.Errorf("failed to save new login: %w", err)
	}
	client := ul.Client.(*TelegramClient)
	client.isNewLogin = true
	client.Connect(ul.Log.WithContext(bl.main.Bridge.BackgroundCtx))

	bgCtx := ul.Log.WithContext(bl.main.Bridge.BackgroundCtx)
	go func() {
		if metadata.IsBot {
			return
		}
		log := ul.Log.With().Str("action", "post-login sync").Logger()
		err := client.clientInitialized.Wait(bgCtx)
		if err != nil {
			log.Err(err).Msg("Failed to wait for client init to sync chats after login")
		} else if client.clientDone.IsSet() {
			log.Warn().Msg("Client is already done after login, skipping chat sync")
		} else if err = client.syncChats(log.WithContext(client.clientCtx), 0, true, false); err != nil {
			log.Err(err).Msg("Failed to sync chats")
		}
	}()

	go func() {
		if metadata.IsBot {
			return
		}
		if !bl.main.Config.Takeout.BackwardBackfill && !bl.main.Config.Takeout.ForwardBackfill && !bl.main.Config.Takeout.DialogSync {
			return
		}
		log := ul.Log.With().Str("component", "post-login takeout").Logger()
		client.takeoutLock.Lock()
		defer client.takeoutLock.Unlock()
		err := client.clientInitialized.Wait(bgCtx)
		if err != nil {
			log.Err(err).Msg("Failed to wait for client init to start takeout")
		} else if client.clientDone.IsSet() {
			log.Warn().Msg("Client is already done after login, skipping takeout")
		} else if _, err = client.getTakeoutID(bgCtx); err != nil {
			log.Err(err).Msg("Failed to get takeout")
		} else if client.stopTakeoutTimer == nil {
			client.stopTakeoutTimer = time.AfterFunc(max(time.Hour, time.Duration(client.main.Bridge.Config.Backfill.Queue.BatchDelay*2)), sync.OnceFunc(func() {
				err := client.stopTakeout(bgCtx)
				if err != nil {
					log.Err(err).Msg("Error stopping takeout in timer started after login")
				}
			}))
		} else {
			client.stopTakeoutTimer.Reset(max(time.Hour, time.Duration(client.main.Bridge.Config.Backfill.Queue.BatchDelay*2)))
		}
	}()

	return &bridgev2.LoginStep{
		Type:         bridgev2.LoginStepTypeComplete,
		StepID:       LoginStepIDComplete,
		Instructions: fmt.Sprintf("Successfully logged in as %s (`%d`)", ul.RemoteName, self.ID),
		CompleteParams: &bridgev2.LoginCompleteParams{
			UserLoginID: ul.ID,
			UserLogin:   ul,
		},
	}, nil
}
