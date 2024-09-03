package connector

import (
	"context"
	"fmt"

	"github.com/gotd/td/tg"
)

type hasUserUpdates interface {
	GetUsers() []tg.UserClass
}

type hasUpdates interface {
	hasUserUpdates
	GetChats() []tg.ChatClass
}

func APICallWithOnlyUserUpdates[U hasUserUpdates](ctx context.Context, t *TelegramClient, fn func() (U, error)) (U, error) {
	resp, err := fn()
	if err != nil {
		return *new(U), err
	}

	for _, user := range resp.GetUsers() {
		user, ok := user.(*tg.User)
		if !ok {
			return *new(U), fmt.Errorf("user is %T not *tg.User", user)
		}
		_, err := t.updateGhost(ctx, user.ID, user)
		if err != nil {
			return *new(U), err
		}
	}

	return resp, nil
}

// Wrapper for API calls that return a response with updates.
func APICallWithUpdates[U hasUpdates](ctx context.Context, t *TelegramClient, fn func() (U, error)) (U, error) {
	resp, err := APICallWithOnlyUserUpdates(ctx, t, fn)
	if err != nil {
		return *new(U), err
	}

	for _, c := range resp.GetChats() {
		if channel, ok := c.(*tg.Channel); ok {
			if err := t.updateChannel(ctx, channel); err != nil {
				return *new(U), err
			}
		}
	}

	return resp, nil
}
