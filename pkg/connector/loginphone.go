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

	"github.com/rs/zerolog"
	"maunium.net/go/mautrix/bridgev2"

	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/auth"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

const (
	LoginStepIDPhoneNumber       = "fi.mau.telegram.login.phone_number"
	LoginStepIDCode              = "fi.mau.telegram.login.code"
	LoginStepIDCodeIncorrect     = "fi.mau.telegram.login.code.incorrect"
	LoginStepIDPassword          = "fi.mau.telegram.login.password"
	LoginStepIDPasswordIncorrect = "fi.mau.telegram.login.password.incorrect"
)

type PhoneLogin struct {
	*baseLogin
	phone         string
	hash          string
	codeSubmitted bool
}

var (
	_ bridgev2.LoginProcessUserInput    = (*PhoneLogin)(nil)
	_ bridgev2.LoginProcessWithOverride = (*PhoneLogin)(nil)
)

func (pl *PhoneLogin) StartWithOverride(ctx context.Context, override *bridgev2.UserLogin) (*bridgev2.LoginStep, error) {
	meta := override.Metadata.(*UserLoginMetadata)
	if meta.IsBot {
		return nil, fmt.Errorf("can't re-login to a bot account with phone login")
	}
	phone := cmp.Or(meta.LoginPhone, override.RemoteProfile.Phone)
	if phone != "" {
		zerolog.Ctx(ctx).Debug().Str("phone_number", phone).Msg("Using existing phone number for relogin")
		return pl.submitNumber(ctx, phone)
	}
	zerolog.Ctx(ctx).Debug().Msg("No existing phone number for relogin, re-prompting")
	return pl.Start(ctx)
}

func (pl *PhoneLogin) Start(ctx context.Context) (*bridgev2.LoginStep, error) {
	return &bridgev2.LoginStep{
		Type:   bridgev2.LoginStepTypeUserInput,
		StepID: LoginStepIDPhoneNumber,
		UserInputParams: &bridgev2.LoginUserInputParams{
			Fields: []bridgev2.LoginInputDataField{{
				Type:        bridgev2.LoginInputFieldTypePhoneNumber,
				ID:          LoginStepIDPhoneNumber,
				Name:        "Phone number",
				Description: "Include the country code with +",
			}},
		},
	}, nil
}

func (pl *PhoneLogin) SubmitUserInput(ctx context.Context, input map[string]string) (*bridgev2.LoginStep, error) {
	if pl.client == nil {
		return pl.submitNumber(ctx, input[LoginStepIDPhoneNumber])
	} else if pl.codeSubmitted {
		return pl.submitPassword(ctx, input[LoginStepIDPassword], pl.phone)
	} else {
		return pl.submitCode(ctx, input[LoginStepIDCode])
	}
}

var phoneLoginStep = &bridgev2.LoginStep{
	Type:   bridgev2.LoginStepTypeUserInput,
	StepID: LoginStepIDCode,
	UserInputParams: &bridgev2.LoginUserInputParams{
		Fields: []bridgev2.LoginInputDataField{{
			Type:        bridgev2.LoginInputFieldType2FACode,
			ID:          LoginStepIDCode,
			Name:        "Code",
			Description: "The code was sent to the Telegram app on your phone",
		}},
	},
}

var phoneCodeIncorrectStep = &bridgev2.LoginStep{
	Type:         bridgev2.LoginStepTypeUserInput,
	StepID:       LoginStepIDCodeIncorrect,
	Instructions: "Incorrect code",
	UserInputParams: &bridgev2.LoginUserInputParams{
		Fields: []bridgev2.LoginInputDataField{{
			Type: bridgev2.LoginInputFieldType2FACode,
			ID:   LoginStepIDCode,
			Name: "Code",
		}},
	},
}

func (pl *PhoneLogin) submitNumber(ctx context.Context, phone string) (*bridgev2.LoginStep, error) {
	if phone == "" {
		return nil, ErrPhoneNumberNotProvided
	}
	log := zerolog.Ctx(ctx).With().Str("component", "phone login").Logger()
	ctx = log.WithContext(ctx)
	pl.phone = phone
	err := pl.makeClient(ctx, nil)
	if err != nil {
		return nil, err
	}

	sentCode, err := pl.client.Auth().SendCode(ctx, pl.phone, auth.SendCodeOptions{})
	if err != nil {
		return nil, loginRespError(err)
	}
	switch s := sentCode.(type) {
	case *tg.AuthSentCode:
		pl.hash = s.PhoneCodeHash
		return phoneLoginStep, nil
	case *tg.AuthSentCodeSuccess:
		switch authorization := s.Authorization.(type) {
		case *tg.AuthAuthorization:
			return pl.finalizeLogin(ctx, authorization, &UserLoginMetadata{LoginPhone: pl.phone})
		case *tg.AuthAuthorizationSignUpRequired:
			return nil, ErrSignUpNotSupported
		default:
			return nil, fmt.Errorf("unexpected authorization type: %T", sentCode)
		}
	default:
		return nil, fmt.Errorf("unexpected sent code type: %T", sentCode)
	}
}

func (pl *PhoneLogin) submitCode(ctx context.Context, code string) (*bridgev2.LoginStep, error) {
	if pl.client == nil {
		return nil, fmt.Errorf("unexpected state: client is nil when submitting phone code")
	}
	authorization, err := pl.client.Auth().SignIn(ctx, pl.phone, code, pl.hash)
	if errors.Is(err, auth.ErrPasswordAuthNeeded) {
		pl.codeSubmitted = true
		return passwordLoginStep, nil
	} else if errors.Is(err, auth.ErrPhoneCodeInvalid) {
		return phoneCodeIncorrectStep, nil
	} else if errors.Is(err, &auth.SignUpRequired{}) {
		return nil, ErrSignUpNotSupported
	} else if err != nil {
		return nil, loginRespError(fmt.Errorf("failed to submit code: %w", err))
	}
	return pl.finalizeLogin(ctx, authorization, &UserLoginMetadata{LoginPhone: pl.phone})
}
