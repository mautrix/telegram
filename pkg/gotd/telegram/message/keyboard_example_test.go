package message_test

import (
	"context"
	"fmt"
	"os"
	"os/signal"

	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram"
	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/message"
	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/message/markup"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

func sendKeyboard(ctx context.Context) error {
	client, err := telegram.ClientFromEnvironment(telegram.Options{})
	if err != nil {
		return err
	}

	return client.Run(ctx, func(ctx context.Context) error {
		sender := message.NewSender(tg.NewClient(client))

		// Uploads and sends keyboard result to the @durovschat.
		if _, err := sender.Resolve("@durovschat").Row(
			markup.URL("Blue", "https://github.com/xelaj/mtproto"),
			markup.URL("Red", "https://go.mau.fi/mautrix-telegram/pkg/gotd"),
		).Text(ctx, "Choose the pill"); err != nil {
			return err
		}

		return nil
	})
}

func ExampleKeyboard() {
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt)
	defer cancel()

	if err := sendKeyboard(ctx); err != nil {
		_, _ = fmt.Fprintf(os.Stderr, "%+v\n", err)
		os.Exit(2)
	}
}
