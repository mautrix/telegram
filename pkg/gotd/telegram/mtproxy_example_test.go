package telegram_test

import (
	"context"
	"encoding/hex"
	"fmt"
	"os"
	"os/signal"

	"github.com/go-faster/errors"

	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram"
	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/dcs"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

func connectViaMTProxy(ctx context.Context) error {
	secret, err := hex.DecodeString(os.Getenv("SECRET"))
	if err != nil {
		return errors.Wrap(err, "parse secret")
	}

	resolver, err := dcs.MTProxy(
		os.Getenv("PROXY_ADDR"),
		secret,
		dcs.MTProxyOptions{},
	)
	if err != nil {
		return errors.Wrap(err, "create MTProxy resolver")
	}

	client, err := telegram.ClientFromEnvironment(telegram.Options{
		Resolver: resolver,
	})
	if err != nil {
		return errors.Wrap(err, "create client")
	}

	return client.Run(ctx, func(ctx context.Context) error {
		cfg, err := tg.NewClient(client).HelpGetConfig(ctx)
		if err != nil {
			return errors.Wrap(err, "get config")
		}

		fmt.Println("This DC: ", cfg.ThisDC)
		return nil
	})
}

func ExampleClient_mtproxy() {
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt)
	defer cancel()

	if err := connectViaMTProxy(ctx); err != nil {
		_, _ = fmt.Fprintf(os.Stderr, "%+v\n", err)
		os.Exit(1)
	}
}
