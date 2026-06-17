// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2026 Tulir Asokan
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
	"encoding/json"
	"fmt"

	"github.com/rs/zerolog"
	"maunium.net/go/mautrix/bridgev2"
)

const (
	LoginStepIDManualSession = "fi.mau.telegram.login.manual_session"
)

type ManualLogin struct {
	*baseLogin
}

var _ bridgev2.LoginProcessUserInput = (*ManualLogin)(nil)

func (ml *ManualLogin) Start(ctx context.Context) (*bridgev2.LoginStep, error) {
	return &bridgev2.LoginStep{
		Type:         bridgev2.LoginStepTypeUserInput,
		StepID:       LoginStepIDManualSession,
		Instructions: "",
		UserInputParams: &bridgev2.LoginUserInputParams{
			Fields: []bridgev2.LoginInputDataField{{
				Type:    bridgev2.LoginInputFieldTypeToken,
				ID:      LoginStepIDManualSession,
				Name:    "Session data JSON",
				Pattern: `^\{[a-zA-Z0-9/+=":, ]+\}$`,
			}},
		},
	}, nil
}

func (ml *ManualLogin) SubmitUserInput(ctx context.Context, input map[string]string) (*bridgev2.LoginStep, error) {
	log := zerolog.Ctx(ctx).With().Str("component", "manual login").Logger()
	ctx = log.WithContext(ctx)

	err := json.Unmarshal([]byte(input[LoginStepIDManualSession]), &ml.session)
	if err != nil {
		return nil, fmt.Errorf("failed to parse session data: %w", err)
	} else if !ml.session.HasAuthKey() || ml.session.Datacenter == 0 || ml.session.ServerAddress == "" {
		return nil, fmt.Errorf("session data is missing required fields")
	}
	err = ml.makeClient(ctx, nil)
	if err != nil {
		return nil, err
	}
	return ml.finalizeLogin(ctx, nil, nil)
}
