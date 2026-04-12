// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2025 Tulir Asokan
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
	"net/http"

	"github.com/rs/zerolog"
	"maunium.net/go/mautrix/bridgev2"
)

const (
	LoginStepIDBotToken = "fi.mau.telegram.login.bot_token"
)

type BotLogin struct {
	*baseLogin
}

func (bl *BotLogin) StartWithOverride(ctx context.Context, override *bridgev2.UserLogin) (*bridgev2.LoginStep, error) {
	meta := override.Metadata.(*UserLoginMetadata)
	if !meta.IsBot {
		return nil, fmt.Errorf("can't re-login to a non-bot account with bot token")
	}
	return bl.Start(ctx)
}

func (bl *BotLogin) Start(ctx context.Context) (*bridgev2.LoginStep, error) {
	return &bridgev2.LoginStep{
		Type:         bridgev2.LoginStepTypeUserInput,
		StepID:       LoginStepIDBotToken,
		Instructions: "Please enter the bot token you want to log in as",
		UserInputParams: &bridgev2.LoginUserInputParams{
			Fields: []bridgev2.LoginInputDataField{{
				Type:    bridgev2.LoginInputFieldTypeToken,
				ID:      LoginStepIDBotToken,
				Name:    "Bot token",
				Pattern: `^\d+:[A-Za-z0-9_-]{35}$`,
			}},
		},
	}, nil
}

func (bl *BotLogin) SubmitUserInput(ctx context.Context, input map[string]string) (*bridgev2.LoginStep, error) {
	log := zerolog.Ctx(ctx).With().Str("component", "telegram bot login").Logger()
	ctx = log.WithContext(ctx)

	botToken := input[LoginStepIDBotToken]
	dialFunc, err := GetProxyDialFunc(bl.main.Config.ProxyConfig)
	if err != nil {
		return nil, err
	}
	httpClient := &http.Client{
		Transport: &http.Transport{
			DialContext: dialFunc,
		},
	}
	err = logoutBotAPI(ctx, botToken, httpClient)
	if err != nil {
		return nil, fmt.Errorf("failed to logout from bot API: %w", err)
	}

	err = bl.makeClient(ctx, nil)
	if err != nil {
		return nil, err
	}
	authorization, err := bl.client.Auth().Bot(ctx, botToken)
	if err != nil {
		bl.Cancel()
		return nil, err
	}
	return bl.finalizeLogin(ctx, authorization, &UserLoginMetadata{IsBot: true})
}

type botAPIResponse struct {
	OK          bool   `json:"ok"`
	ErrorCode   int    `json:"error_code"`
	Description string `json:"description"`
}

func logoutBotAPI(ctx context.Context, token string, client *http.Client) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, "https://api.telegram.org/bot"+token+"/logOut", nil)
	if err != nil {
		return fmt.Errorf("failed to prepare request: %w", err)
	}
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("failed to send request: %w", err)
	}
	var respData botAPIResponse
	err = json.NewDecoder(resp.Body).Decode(&respData)
	_ = resp.Body.Close()
	if err != nil {
		return fmt.Errorf("failed to decode response: %w", err)
	} else if !respData.OK && respData.Description != "Logged out" {
		return fmt.Errorf("response error %d: %s", respData.ErrorCode, respData.Description)
	}
	return nil
}

var _ bridgev2.LoginProcessUserInput = (*BotLogin)(nil)
