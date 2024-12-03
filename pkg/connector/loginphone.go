package connector

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/gotd/td/telegram"
	"github.com/gotd/td/telegram/auth"
	"github.com/gotd/td/tg"
	"github.com/rs/zerolog"
	"go.mau.fi/zerozap"
	"go.uber.org/zap"
	"maunium.net/go/mautrix/bridgev2"
)

const (
	LoginStepIDPhoneNumber = "fi.mau.telegram.login.phone_number"
	LoginStepIDCode        = "fi.mau.telegram.login.code"
	LoginStepIDPassword    = "fi.mau.telegram.login.password"
)

type PhoneLogin struct {
	user             *bridgev2.User
	main             *TelegramConnector
	authData         UserLoginSession
	authClient       *telegram.Client
	authClientCancel context.CancelFunc

	phone string
	hash  string
}

var _ bridgev2.LoginProcessUserInput = (*PhoneLogin)(nil)

func (p *PhoneLogin) Cancel() {
	if p.authClientCancel != nil {
		p.authClientCancel()
	}
}

func (p *PhoneLogin) Start(ctx context.Context) (*bridgev2.LoginStep, error) {
	return &bridgev2.LoginStep{
		Type:         bridgev2.LoginStepTypeUserInput,
		StepID:       LoginStepIDPhoneNumber,
		Instructions: "Please enter your phone number",
		UserInputParams: &bridgev2.LoginUserInputParams{
			Fields: []bridgev2.LoginInputDataField{
				{
					Type:        bridgev2.LoginInputFieldTypePhoneNumber,
					ID:          LoginStepIDPhoneNumber,
					Name:        "Phone Number",
					Description: "Include the country code with +",
				},
			},
		},
	}, nil
}

func (p *PhoneLogin) SubmitUserInput(ctx context.Context, input map[string]string) (*bridgev2.LoginStep, error) {
	log := zerolog.Ctx(ctx).With().Str("component", "telegram_phone_login").Logger()
	if phone, ok := input[LoginStepIDPhoneNumber]; ok {
		p.phone = phone
		p.authClient = telegram.NewClient(p.main.Config.APIID, p.main.Config.APIHash, telegram.Options{
			CustomSessionStorage: &p.authData,
			Logger:               zap.New(zerozap.New(zerolog.Ctx(ctx).With().Str("component", "telegram_phone_login_client").Logger())),
		})
		var err error
		authClientContext, _ := context.WithTimeoutCause(log.WithContext(context.Background()), time.Hour, errors.New("phone login took over one hour"))
		_, p.authClientCancel, err = connectTelegramClient(authClientContext, p.authClient)
		if err != nil {
			return nil, err
		}
		sentCode, err := p.authClient.Auth().SendCode(ctx, p.phone, auth.SendCodeOptions{})
		if err != nil {
			return nil, err
		}
		switch s := sentCode.(type) {
		case *tg.AuthSentCode:
			p.hash = s.PhoneCodeHash
			return &bridgev2.LoginStep{
				Type:         bridgev2.LoginStepTypeUserInput,
				StepID:       LoginStepIDCode,
				Instructions: "Please enter the code sent to your phone",
				UserInputParams: &bridgev2.LoginUserInputParams{
					Fields: []bridgev2.LoginInputDataField{
						{
							Type: bridgev2.LoginInputFieldType2FACode,
							ID:   LoginStepIDCode,
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
	} else if code, ok := input[LoginStepIDCode]; ok {
		authorization, err := p.authClient.Auth().SignIn(ctx, p.phone, code, p.hash)
		if errors.Is(err, auth.ErrPasswordAuthNeeded) {
			return &bridgev2.LoginStep{
				Type:         bridgev2.LoginStepTypeUserInput,
				StepID:       LoginStepIDPassword,
				Instructions: "Please enter your password",
				UserInputParams: &bridgev2.LoginUserInputParams{
					Fields: []bridgev2.LoginInputDataField{
						{
							Type: bridgev2.LoginInputFieldTypePassword,
							ID:   LoginStepIDPassword,
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
	} else if password, ok := input[LoginStepIDPassword]; ok {
		authorization, err := p.authClient.Auth().Password(ctx, password)
		if err != nil {
			return nil, fmt.Errorf("failed to submit password: %w", err)
		}
		return p.handleAuthSuccess(ctx, authorization)
	}

	return nil, fmt.Errorf("unexpected state during phone login")
}

func (p *PhoneLogin) handleAuthSuccess(ctx context.Context, authorization *tg.AuthAuthorization) (*bridgev2.LoginStep, error) {
	// Stop the login client.
	p.authClientCancel()

	return finalizeLogin(ctx, p.user, authorization, UserLoginMetadata{
		Phone:   p.phone,
		Session: p.authData,
	})
}
