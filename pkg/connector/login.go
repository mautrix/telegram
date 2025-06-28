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
	"fmt"
	"strings"

	"maunium.net/go/mautrix/bridgev2"
	"maunium.net/go/mautrix/bridgev2/database"
	"maunium.net/go/mautrix/bridgev2/status"

	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
	"go.mau.fi/mautrix-telegram/pkg/connector/util"
)

const (
	LoginFlowIDPhone = "phone"
	LoginFlowIDQR    = "qr"

	LoginStepIDComplete = "fi.mau.telegram.login.complete"
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
	}
}

func (tg *TelegramConnector) CreateLogin(ctx context.Context, user *bridgev2.User, flowID string) (bridgev2.LoginProcess, error) {
	switch flowID {
	case LoginFlowIDPhone:
		return &PhoneLogin{user: user, main: tg}, nil
	case LoginFlowIDQR:
		return &QRLogin{user: user, main: tg}, nil
	default:
		return nil, fmt.Errorf("unknown flow ID %s", flowID)
	}
}

func finalizeLogin(ctx context.Context, user *bridgev2.User, authorization *tg.AuthAuthorization, metadata UserLoginMetadata) (*bridgev2.LoginStep, error) {
	userLoginID := ids.MakeUserLoginID(authorization.User.GetID())
	ul, err := user.NewLogin(ctx, &database.UserLogin{
		ID:       userLoginID,
		Metadata: &metadata,
	}, &bridgev2.NewLoginParams{
		DeleteOnConflict: true,
	})
	if err != nil {
		return nil, fmt.Errorf("failed to save new login: %w", err)
	}
	ul.Client.Connect(ul.Log.WithContext(context.Background()))
	client := ul.Client.(*TelegramClient)
	// Connecting is non-blocking so wait for gotd to initialize before doing anythign to avoid deadlocking
	select {
	case <-client.initialized:
	case <-ctx.Done():
		return nil, ctx.Err()
	}
	me, err := client.client.Self(ctx)
	if err != nil {
		return nil, err
	}
	go func() {
		log := ul.Log.With().Str("component", "login_sync_chats").Logger()
		if err := client.SyncChats(log.WithContext(context.Background())); err != nil {
			log.Err(err).Msg("Failed to sync chats")
		}
	}()

	fullName := util.FormatFullName(me.FirstName, me.LastName, me.Deleted, me.ID)
	username := me.Username
	if username == "" && len(me.Usernames) > 0 {
		username = me.Usernames[0].Username
	}
	normalizedPhone := "+" + strings.TrimPrefix(me.Phone, "+")
	remoteName := username
	if remoteName == "" {
		remoteName = normalizedPhone
	}
	if remoteName == "" {
		remoteName = fullName
	}
	ul.RemoteName = remoteName
	ul.RemoteProfile = status.RemoteProfile{
		Phone:    me.Phone,
		Username: username,
		Name:     fullName,
	}
	err = ul.Save(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to save login: %w", err)
	}

	return &bridgev2.LoginStep{
		Type:         bridgev2.LoginStepTypeComplete,
		StepID:       LoginStepIDComplete,
		Instructions: fmt.Sprintf("Successfully logged in as %d / +%s (%s)", me.ID, me.Phone, remoteName),
		CompleteParams: &bridgev2.LoginCompleteParams{
			UserLoginID: ul.ID,
			UserLogin:   ul,
		},
	}, nil
}
