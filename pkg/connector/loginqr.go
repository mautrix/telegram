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
	"time"

	"github.com/rs/zerolog"
	"maunium.net/go/mautrix/bridgev2"

	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/auth/qrlogin"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tgerr"
)

type qrAuthResult struct {
	Authorization *tg.AuthAuthorization
	Error         error
}

type QRLogin struct {
	*baseLogin
	auth    chan qrAuthResult
	qrToken chan qrlogin.Token
}

const LoginStepIDShowQR = "fi.mau.telegram.login.show_qr"

var _ bridgev2.LoginProcessDisplayAndWait = (*QRLogin)(nil) // For showing QR code
var _ bridgev2.LoginProcessUserInput = (*QRLogin)(nil)      // For asking for password

func waitContextDone(ctx context.Context) error {
	<-ctx.Done()
	return ctx.Err()
}

const LoginTimeout = 10 * time.Minute

var ErrLoginTimeout = errors.New("login process timed out")

func (ql *QRLogin) StartWithOverride(ctx context.Context, override *bridgev2.UserLogin) (*bridgev2.LoginStep, error) {
	meta := override.Metadata.(*UserLoginMetadata)
	if meta.IsBot {
		return nil, fmt.Errorf("can't re-login to a bot account with QR login")
	}
	return ql.Start(ctx)
}

func (ql *QRLogin) Start(ctx context.Context) (*bridgev2.LoginStep, error) {
	log := zerolog.Ctx(ctx).With().Str("component", "qr login").Logger()
	ctx = log.WithContext(ctx)

	loggedIn := make(chan struct{})
	dispatcher := tg.NewUpdateDispatcher()
	dispatcher.OnLoginToken(func(ctx context.Context, e tg.Entities, update *tg.UpdateLoginToken) error {
		log.Debug().Msg("Received updateLoginToken")
		close(loggedIn)
		return nil
	})
	err := ql.makeClient(ctx, &dispatcher)
	if err != nil {
		return nil, err
	}

	ql.qrToken = make(chan qrlogin.Token)
	ql.auth = make(chan qrAuthResult)
	go func() {
		auth, err := ql.client.QR().Auth(ql.ctx, loggedIn, func(ctx context.Context, token qrlogin.Token) error {
			ql.qrToken <- token
			return nil
		})

		ql.auth <- qrAuthResult{auth, err}
	}()

	// Wait for the first QR token and show it to the user.:
	select {
	case token := <-ql.qrToken:
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
		ql.Cancel()
		return nil, ctx.Err()
	case <-ql.ctx.Done():
		return nil, ql.ctx.Err()
	}
}

func (ql *QRLogin) Wait(ctx context.Context) (*bridgev2.LoginStep, error) {
	if ql.qrToken == nil {
		panic("qr token channel is nil")
	}

	select {
	case token := <-ql.qrToken:
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
	case authResult := <-ql.auth:
		if tgerr.Is(authResult.Error, "SESSION_PASSWORD_NEEDED") {
			return passwordLoginStep, nil
		} else if authResult.Error != nil {
			ql.Cancel()
			return nil, fmt.Errorf("failed to authenticate: %w", authResult.Error)
		}

		return ql.finalizeLogin(ctx, authResult.Authorization, nil)
	case <-ctx.Done():
		ql.Cancel()
		return nil, ctx.Err()
	case <-ql.ctx.Done():
		return nil, ql.ctx.Err()
	}
}

func (ql *QRLogin) SubmitUserInput(ctx context.Context, input map[string]string) (*bridgev2.LoginStep, error) {
	return ql.submitPassword(ctx, input[LoginStepIDPassword], "")
}
