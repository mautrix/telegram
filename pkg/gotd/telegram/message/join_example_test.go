package message_test

import (
	"context"
	"fmt"
	"os"
	"os/signal"

	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram"
	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/message"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

func sendJoin(ctx context.Context) error {
	client, err := telegram.ClientFromEnvironment(telegram.Options{})
	if err != nil {
		return err
	}

	return client.Run(ctx, func(ctx context.Context) error {
		sender := message.NewSender(tg.NewClient(client))

		// Join to private chat by link.
		if _, err := sender.JoinLink(ctx, "https://t.me/+aBCdeFG123AAAAAA"); err != nil {
			return err
		}

		return nil
	})
}

func ExampleSender_JoinLink() {
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt)
	defer cancel()

	if err := sendJoin(ctx); err != nil {
		_, _ = fmt.Fprintf(os.Stderr, "%+v\n", err)
		os.Exit(2)
	}
}
