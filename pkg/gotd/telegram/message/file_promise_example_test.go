package message_test

import (
	"context"
	"fmt"
	"os"
	"os/signal"

	"github.com/go-faster/errors"

	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram"
	"go.mau.fi/mautrix-telegram/pkg/gotd/telegram/message"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
)

func filePromiseResult(ctx context.Context) error {
	client, err := telegram.ClientFromEnvironment(telegram.Options{})
	if err != nil {
		return err
	}

	return client.Run(ctx, func(ctx context.Context) error {
		sender := message.NewSender(tg.NewClient(client))
		r := sender.Resolve("@durov")

		var result tg.InputFileClass
		_, err := r.Upload(message.Upload(func(ctx context.Context, b message.Uploader) (tg.InputFileClass, error) {
			r, err := b.FromPath(ctx, "file.jpg", "")
			if err != nil {
				return nil, err
			}

			result = r
			return r, nil
		})).Photo(ctx)
		if err != nil {
			return errors.Wrap(err, "upload photo")
		}

		_, err = r.Media(ctx, message.UploadedDocument(result))
		if err != nil {
			return errors.Wrap(err, "upload document")
		}

		return nil
	})
}

func ExampleUpload() {
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt)
	defer cancel()

	if err := filePromiseResult(ctx); err != nil {
		_, _ = fmt.Fprintf(os.Stderr, "%+v\n", err)
		os.Exit(2)
	}
}
