package connector

import (
	"context"
	"fmt"

	"github.com/gotd/td/tg"
)

type hasUpdates interface {
	GetUsers() []tg.UserClass
}

// Wrapper for API calls that return a response with updates.
func APICallWithUpdates[U hasUpdates](ctx context.Context, t *TelegramClient, fn func() (U, error)) (U, error) {
	resp, err := fn()
	if err != nil {
		return resp, err
	}

	// TODO do we also need to expand this to chats and messages?
	for _, user := range resp.GetUsers() {
		user, ok := user.(*tg.User)
		if !ok {
			return resp, fmt.Errorf("user is %T not *tg.User", user)
		}
		_, err := t.updateGhost(ctx, user.ID, user)
		if err != nil {
			return resp, err
		}
	}

	return resp, nil
}
