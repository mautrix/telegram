package connector

import (
	"context"
	"fmt"

	"github.com/gotd/td/telegram"
	"github.com/gotd/td/telegram/auth/qrlogin"
	"github.com/gotd/td/telegram/updates"
	"github.com/gotd/td/tg"
	"github.com/gotd/td/tgerr"
	"github.com/rs/zerolog"
	"go.mau.fi/zerozap"
	"go.uber.org/zap"
	"maunium.net/go/mautrix/bridgev2"
)

type qrAuthResult struct {
	PasswordNeeded bool
	Authorization  *tg.AuthAuthorization
	Error          error
}

type QRLogin struct {
	user       *bridgev2.User
	main       *TelegramConnector
	authData   UserLoginSession
	authClient *telegram.Client

	authClientCtx    context.Context
	authClientCancel context.CancelFunc

	auth    chan qrAuthResult
	qrToken chan qrlogin.Token
}

const LoginStepIDShowQR = "fi.mau.telegram.login.show_qr"

var _ bridgev2.LoginProcessDisplayAndWait = (*QRLogin)(nil) // For showing QR code
var _ bridgev2.LoginProcessUserInput = (*QRLogin)(nil)      // For asking for password

func (q *QRLogin) Cancel() {
	if q.authClientCancel != nil {
		q.authClientCancel()
	}
}

func (q *QRLogin) Start(ctx context.Context) (*bridgev2.LoginStep, error) {
	log := zerolog.Ctx(ctx).With().Str("component", "telegram_qr_login").Logger()
	loggedIn := make(chan struct{})

	dispatcher := tg.NewUpdateDispatcher()
	dispatcher.OnLoginToken(func(ctx context.Context, e tg.Entities, update *tg.UpdateLoginToken) error {
		loggedIn <- struct{}{}
		return nil
	})
	zaplog := zap.New(zerozap.New(log))
	updateManager := updates.New(updates.Config{
		Handler: dispatcher,
		Logger:  zaplog.Named("login_update_manager"),
	})
	q.authClient = telegram.NewClient(q.main.Config.APIID, q.main.Config.APIHash, telegram.Options{
		CustomSessionStorage: &q.authData,
		UpdateHandler:        updateManager,
		Logger:               zaplog,
	})

	var err error
	q.authClientCtx, q.authClientCancel, err = connectTelegramClient(log.WithContext(context.Background()), q.authClient)
	if err != nil {
		return nil, err
	}

	qr := qrlogin.NewQR(q.authClient.API(), q.main.Config.APIID, q.main.Config.APIHash, qrlogin.Options{
		Migrate: q.authClient.MigrateTo,
	})
	q.qrToken = make(chan qrlogin.Token)
	q.auth = make(chan qrAuthResult)
	go func() {
		auth, err := qr.Auth(q.authClientCtx, loggedIn, func(ctx context.Context, token qrlogin.Token) error {
			q.qrToken <- token
			return nil
		})

		q.auth <- qrAuthResult{false, auth, err}
	}()

	// Wait for the first QR token and show it to the user.:
	select {
	case token := <-q.qrToken:
		return &bridgev2.LoginStep{
			Type:         bridgev2.LoginStepTypeDisplayAndWait,
			StepID:       LoginStepIDShowQR,
			Instructions: "Scan the QR code on your phone to log in",
			DisplayAndWaitParams: &bridgev2.LoginDisplayAndWaitParams{
				Type: bridgev2.LoginDisplayTypeQR,
				Data: token.URL(),
			},
		}, nil
	case <-ctx.Done():
		q.Cancel()
		return nil, ctx.Err()
	case <-q.authClientCtx.Done():
		return nil, q.authClientCtx.Err()
	}
}

func (q *QRLogin) Wait(ctx context.Context) (*bridgev2.LoginStep, error) {
	if q.qrToken == nil {
		panic("qr token channel is nil")
	}

	select {
	case token := <-q.qrToken:
		// There's a new token, show it to the user.
		return &bridgev2.LoginStep{
			Type:         bridgev2.LoginStepTypeDisplayAndWait,
			StepID:       LoginStepIDShowQR,
			Instructions: "Scan the QR code on your phone to log in",
			DisplayAndWaitParams: &bridgev2.LoginDisplayAndWaitParams{
				Type: bridgev2.LoginDisplayTypeQR,
				Data: token.URL(),
			},
		}, nil
	case authResult := <-q.auth:
		if tgerr.Is(authResult.Error, "SESSION_PASSWORD_NEEDED") {
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
		} else if authResult.Error != nil {
			return nil, fmt.Errorf("failed to authenticate: %w", authResult.Error)
		}

		// Stop the login client
		q.authClientCancel()

		return finalizeLogin(ctx, q.user, authResult.Authorization, UserLoginMetadata{
			Session: q.authData,
		})
	case <-ctx.Done():
		q.Cancel()
		return nil, ctx.Err()
	case <-q.authClientCtx.Done():
		return nil, q.authClientCtx.Err()
	}
}

func (q *QRLogin) SubmitUserInput(ctx context.Context, input map[string]string) (*bridgev2.LoginStep, error) {
	password, ok := input[LoginStepIDPassword]
	if !ok {
		return nil, fmt.Errorf("unexpected state during phone login")
	}
	authorization, err := q.authClient.Auth().Password(ctx, password)
	if err != nil {
		return nil, fmt.Errorf("failed to submit password: %w", err)
	}

	// Stop the login client
	q.authClientCancel()

	return finalizeLogin(ctx, q.user, authorization, UserLoginMetadata{
		Session: q.authData,
	})
}
