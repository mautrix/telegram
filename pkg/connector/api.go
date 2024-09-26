package connector

import (
	"context"
	"fmt"

	"github.com/gotd/td/tg"
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
			if err := t.updateChannel(ctx, channel); err != nil {
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
