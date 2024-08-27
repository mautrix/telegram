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
	"context"
	"errors"
	"fmt"

	"github.com/gotd/td/telegram"
	"github.com/gotd/td/telegram/auth"
	"github.com/gotd/td/tg"
	"github.com/rs/zerolog"
	"go.mau.fi/zerozap"
	"go.uber.org/zap"
	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/connector/util"
)

// TODO QR login support

const LoginFlowIDPhone = "phone"

func (tg *TelegramConnector) GetLoginFlows() []bridgev2.LoginFlow {
	return []bridgev2.LoginFlow{{
		Name:        "Phone Number",
		Description: "Login using your Telegram phone number",
		ID:          LoginFlowIDPhone,
	}}
}

func (tg *TelegramConnector) CreateLogin(ctx context.Context, user *bridgev2.User, flowID string) (bridgev2.LoginProcess, error) {
	if flowID != LoginFlowIDPhone {
		return nil, fmt.Errorf("unknown flow ID %s", flowID)
	}
	return &PhoneLogin{user: user, main: tg}, nil
}

const (
	phoneNumberStep = "fi.mau.telegram.phone_number"
	codeStep        = "fi.mau.telegram.code"
	passwordStep    = "fi.mau.telegram.password"
	completeStep    = "fi.mau.telegram.complete"
)

type PhoneLogin struct {
	user         *bridgev2.User
	main         *TelegramConnector
	authData     UserLoginSession
	client       *telegram.Client
	clientCancel context.CancelFunc

	phone string
	hash  string
}

var _ bridgev2.LoginProcessUserInput = (*PhoneLogin)(nil)

func (p *PhoneLogin) Cancel() {
	p.clientCancel()
}

func (p *PhoneLogin) Start(ctx context.Context) (*bridgev2.LoginStep, error) {
	return &bridgev2.LoginStep{
		Type:         bridgev2.LoginStepTypeUserInput,
		StepID:       phoneNumberStep,
		Instructions: "Please enter your phone number",
		UserInputParams: &bridgev2.LoginUserInputParams{
			Fields: []bridgev2.LoginInputDataField{
				{
					Type:        bridgev2.LoginInputFieldTypePhoneNumber,
					ID:          phoneNumberStep,
					Name:        "Phone Number",
					Description: "Include the country code with +",
				},
			},
		},
	}, nil
}

func (p *PhoneLogin) SubmitUserInput(ctx context.Context, input map[string]string) (*bridgev2.LoginStep, error) {
	if phone, ok := input[phoneNumberStep]; ok {
		p.phone = phone
		p.client = telegram.NewClient(p.main.Config.APIID, p.main.Config.APIHash, telegram.Options{
			CustomSessionStorage: &p.authData,
			Logger:               zap.New(zerozap.New(zerolog.Ctx(ctx).With().Str("component", "telegram_login_client").Logger())),
		})
		var err error
		p.clientCancel, err = connectTelegramClient(context.Background(), p.client)
		if err != nil {
			return nil, err
		}
		sentCode, err := p.client.Auth().SendCode(ctx, p.phone, auth.SendCodeOptions{})
		if err != nil {
			return nil, err
		}
		switch s := sentCode.(type) {
		case *tg.AuthSentCode:
			p.hash = s.PhoneCodeHash
			return &bridgev2.LoginStep{
				Type:         bridgev2.LoginStepTypeUserInput,
				StepID:       codeStep,
				Instructions: "Please enter the code sent to your phone",
				UserInputParams: &bridgev2.LoginUserInputParams{
					Fields: []bridgev2.LoginInputDataField{
						{
							Type: bridgev2.LoginInputFieldType2FACode,
							ID:   codeStep,
							Name: "Code",
						},
					},
				},
			}, nil
		case *tg.AuthSentCodeSuccess:
			switch a := s.Authorization.(type) {
			case *tg.AuthAuthorization:
				// Looks that we are already authorized.
				return p.handleAuthSuccess(ctx, a)
			case *tg.AuthAuthorizationSignUpRequired:
				return nil, fmt.Errorf("phone number does not correspond with an existing Telegram account and sign-up is not supported")
			default:
				return nil, fmt.Errorf("unexpected authorization type: %T", sentCode)
			}
		default:
			return nil, fmt.Errorf("unexpected sent code type: %T", sentCode)
		}
	} else if code, ok := input[codeStep]; ok {
		authorization, err := p.client.Auth().SignIn(ctx, p.phone, code, p.hash)
		if errors.Is(err, auth.ErrPasswordAuthNeeded) {
			return &bridgev2.LoginStep{
				Type:         bridgev2.LoginStepTypeUserInput,
				StepID:       passwordStep,
				Instructions: "Please enter your password",
				UserInputParams: &bridgev2.LoginUserInputParams{
					Fields: []bridgev2.LoginInputDataField{
						{
							Type: bridgev2.LoginInputFieldTypePassword,
							ID:   passwordStep,
							Name: "Password",
						},
					},
				},
			}, nil
		} else if errors.Is(err, &auth.SignUpRequired{}) {
			return nil, fmt.Errorf("sign-up is not supported")
		} else if err != nil {
			return nil, fmt.Errorf("failed to submit code: %w", err)
		}
		return p.handleAuthSuccess(ctx, authorization)
	} else if password, ok := input[passwordStep]; ok {
		authorization, err := p.client.Auth().Password(ctx, password)
		if err != nil {
			return nil, fmt.Errorf("failed to submit password: %w", err)
		}
		return p.handleAuthSuccess(ctx, authorization)
	}

	return nil, fmt.Errorf("unexpected state during phone login")
}

func (p *PhoneLogin) handleAuthSuccess(ctx context.Context, authorization *tg.AuthAuthorization) (*bridgev2.LoginStep, error) {
	// Now that we have the Telegram user ID, store it in the database and
	// close the login client.
	p.clientCancel()

	userLoginID := ids.MakeUserLoginID(authorization.User.GetID())
	ul, err := p.user.NewLogin(ctx, &database.UserLogin{
		ID: userLoginID,
		Metadata: &UserLoginMetadata{
			Phone:   p.phone,
			Session: p.authData,
		},
	}, nil)
	if err != nil {
		return nil, fmt.Errorf("failed to save new login: %w", err)
	}
	err = ul.Client.Connect(ul.Log.WithContext(context.Background()))
	if err != nil {
		return nil, fmt.Errorf("failed to connect after login: %w", err)
	}
	client := ul.Client.(*TelegramClient)
	user, err := client.client.Self(ctx)
	if err != nil {
		return nil, err
	}
	go func() {
		log := ul.Log.With().Str("component", "login_sync_chats").Logger()
		if err := client.SyncChats(log.WithContext(context.Background())); err != nil {
			log.Err(err).Msg("Failed to sync chats")
		}
	}()
	return &bridgev2.LoginStep{
		Type:         bridgev2.LoginStepTypeComplete,
		StepID:       completeStep,
		Instructions: fmt.Sprintf("Successfully logged in as %d / +%s (%s)", user.ID, user.Phone, util.FormatFullName(user.FirstName, user.LastName)),
		CompleteParams: &bridgev2.LoginCompleteParams{
			UserLoginID: ul.ID,
		},
	}, nil
}
