package unpack_test

import (
	"context"
	"fmt"
	"os"
	"os/signal"

	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram"
	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/message"
	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/message/unpack"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

func unpackMessage(ctx context.Context) error {
	client, err := telegram.ClientFromEnvironment(telegram.Options{})
	if err != nil {
		return err
	}

	return client.Run(ctx, func(ctx context.Context) error {
		sender := message.NewSender(tg.NewClient(client))

		msg, err := unpack.Message(sender.Resolve("@durovschat").Dice(ctx))
		// Sends dice "🎲" to the @durovschat.
		if err != nil {
			return err
		}

		fmt.Println("Sent message ID:", msg.ID)

		return nil
	})
}

func ExampleMessage() {
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt)
	defer cancel()

	if err := unpackMessage(ctx); err != nil {
		_, _ = fmt.Fprintf(os.Stderr, "%+v\n", err)
		os.Exit(2)
	}
}
