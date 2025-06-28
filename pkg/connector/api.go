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
	"fmt"

	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

type hasUserUpdates interface {
	GetUsers() []tg.UserClass
}

type hasChatUpdates interface {
	GetChats() []tg.ChatClass
}

type hasUpdates interface {
	hasUserUpdates
	hasChatUpdates
}

func handleUserUpdates[U hasUserUpdates](ctx context.Context, t *TelegramClient, resp hasUserUpdates) error {
	for _, user := range resp.GetUsers() {
		user, ok := user.(*tg.User)
		if !ok {
			return fmt.Errorf("user is %T not *tg.User", user)
		}
		_, err := t.updateGhost(ctx, user.ID, user)
		if err != nil {
			return err
		}
	}
	return nil
}

func handleChatUpdates[U hasChatUpdates](ctx context.Context, t *TelegramClient, resp hasChatUpdates) error {
	for _, c := range resp.GetChats() {
		if channel, ok := c.(*tg.Channel); ok {
			if _, err := t.updateChannel(ctx, channel); err != nil {
				return err
			}
		}
	}
	return nil
}

func APICallWithOnlyUserUpdates[U hasUserUpdates](ctx context.Context, t *TelegramClient, fn func() (U, error)) (U, error) {
	resp, err := fn()
	if err != nil {
		return *new(U), err
	}
	return resp, handleUserUpdates[U](ctx, t, resp)
}

func APICallWithOnlyChatUpdates[U hasChatUpdates](ctx context.Context, t *TelegramClient, fn func() (U, error)) (U, error) {
	resp, err := fn()
	if err != nil {
		return *new(U), err
	}
	return resp, handleChatUpdates[U](ctx, t, resp)
}

// Wrapper for API calls that return a response with updates.
func APICallWithUpdates[U hasUpdates](ctx context.Context, t *TelegramClient, fn func() (U, error)) (U, error) {
	resp, err := fn()
	if err != nil {
		return *new(U), err
	}
	if err = handleUserUpdates[U](ctx, t, resp); err != nil {
		return *new(U), err
	}
	return resp, handleChatUpdates[U](ctx, t, resp)
}
